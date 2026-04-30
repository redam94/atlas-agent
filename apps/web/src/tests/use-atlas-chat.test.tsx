import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { useAtlasChat } from "@/hooks/use-atlas-chat";

class FakeWS {
  static instances: FakeWS[] = [];
  readyState = 0; // CONNECTING
  url: string;
  onopen: ((ev: Event) => void) | null = null;
  onmessage: ((ev: MessageEvent) => void) | null = null;
  onclose: ((ev: CloseEvent) => void) | null = null;
  onerror: ((ev: Event) => void) | null = null;
  sent: string[] = [];

  constructor(url: string) {
    this.url = url;
    FakeWS.instances.push(this);
    queueMicrotask(() => {
      this.readyState = 1; // OPEN
      this.onopen?.(new Event("open"));
    });
  }
  send(data: string) { this.sent.push(data); }
  close() {
    this.readyState = 3;
    this.onclose?.(new CloseEvent("close", { code: 1000 }));
  }
  emit(payload: unknown) {
    this.onmessage?.(new MessageEvent("message", { data: JSON.stringify(payload) }));
  }
  closeUnexpected() {
    this.readyState = 3;
    this.onclose?.(new CloseEvent("close", { code: 1006, wasClean: false }));
  }
}

const createWrapper = () => {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={qc}>{children}</QueryClientProvider>
  );
};

beforeEach(() => {
  // @ts-expect-error swap global WebSocket
  globalThis.WebSocket = FakeWS;
  FakeWS.instances = [];
});

afterEach(() => {
  vi.useRealTimers();
});

describe("useAtlasChat", () => {
  it("appends a blank assistant message on send and accumulates tokens", async () => {
    const { result } = renderHook(
      () => useAtlasChat({ session_id: "S1", project_id: "P1", model_id: "claude-sonnet-4-6" }),
      { wrapper: createWrapper() },
    );

    await waitFor(() => expect(FakeWS.instances.length).toBe(1));
    const ws = FakeWS.instances[0];
    await waitFor(() => expect(ws.readyState).toBe(1));

    act(() => result.current.send("hi"));

    expect(result.current.is_streaming).toBe(true);
    expect(result.current.messages.at(-1)).toMatchObject({ role: "assistant", content: "" });

    act(() => ws.emit({ type: "chat.token", payload: { token: "He" }, sequence: 0 }));
    act(() => ws.emit({ type: "chat.token", payload: { token: "llo" }, sequence: 1 }));

    expect(result.current.messages.at(-1)?.content).toBe("Hello");
  });

  it("finalizes on chat.done and clears is_streaming", async () => {
    const { result } = renderHook(
      () => useAtlasChat({ session_id: "S2", project_id: "P1", model_id: "m" }),
      { wrapper: createWrapper() },
    );
    await waitFor(() => expect(FakeWS.instances.length).toBe(1));
    const ws = FakeWS.instances[0];
    await waitFor(() => expect(ws.readyState).toBe(1));

    act(() => result.current.send("q"));
    act(() => ws.emit({ type: "chat.token", payload: { token: "ok" }, sequence: 0 }));
    act(() => ws.emit({ type: "chat.done", payload: {}, sequence: 1 }));

    expect(result.current.is_streaming).toBe(false);
    expect(result.current.messages.at(-1)?.content).toBe("ok");
  });

  it("captures rag.context citations", async () => {
    const { result } = renderHook(
      () => useAtlasChat({ session_id: "S3", project_id: "P1", model_id: "m" }),
      { wrapper: createWrapper() },
    );
    await waitFor(() => expect(FakeWS.instances.length).toBe(1));
    const ws = FakeWS.instances[0];
    await waitFor(() => expect(ws.readyState).toBe(1));

    act(() => result.current.send("q"));
    act(() =>
      ws.emit({
        type: "rag.context",
        payload: {
          citations: [{ id: 1, title: "T", score: 0.9, chunk_id: "x", text_preview: "..." }],
        },
        sequence: 0,
      }),
    );

    expect(result.current.rag_context).toHaveLength(1);
    expect(result.current.rag_context?.[0].title).toBe("T");
  });

  it("surfaces chat.error and clears streaming", async () => {
    const { result } = renderHook(
      () => useAtlasChat({ session_id: "S4", project_id: "P1", model_id: "m" }),
      { wrapper: createWrapper() },
    );
    await waitFor(() => expect(FakeWS.instances.length).toBe(1));
    const ws = FakeWS.instances[0];
    await waitFor(() => expect(ws.readyState).toBe(1));

    act(() => result.current.send("q"));
    act(() => ws.emit({ type: "chat.error", payload: { code: "x", message: "boom" }, sequence: 0 }));

    expect(result.current.error).toEqual({ code: "x", message: "boom" });
    expect(result.current.is_streaming).toBe(false);
  });

  it("reconnects with backoff on unexpected close", async () => {
    vi.useFakeTimers();
    const { result } = renderHook(
      () => useAtlasChat({ session_id: "S5", project_id: "P1", model_id: "m" }),
      { wrapper: createWrapper() },
    );
    await vi.runAllTimersAsync();
    expect(FakeWS.instances.length).toBe(1);

    act(() => FakeWS.instances[0].closeUnexpected());
    // First retry after ~1s
    await act(async () => { await vi.advanceTimersByTimeAsync(1100); });
    expect(FakeWS.instances.length).toBe(2);

    act(() => FakeWS.instances[1].closeUnexpected());
    // Second retry after ~2s
    await act(async () => { await vi.advanceTimersByTimeAsync(2100); });
    expect(FakeWS.instances.length).toBe(3);

    void result;
  });

  it("appends pending ToolCall on chat.tool_use event", async () => {
    const { result } = renderHook(
      () => useAtlasChat({ session_id: "S6", project_id: "P1", model_id: "m" }),
      { wrapper: createWrapper() },
    );
    await waitFor(() => expect(FakeWS.instances.length).toBe(1));
    const ws = FakeWS.instances[0];
    await waitFor(() => expect(ws.readyState).toBe(1));

    act(() => result.current.send("use the echo tool"));
    act(() =>
      ws.emit({
        type: "chat.tool_use",
        payload: { call_id: "tc_1", tool_name: "fake.echo", started_at: "2026-04-29T10:00:00Z" },
        sequence: 0,
      }),
    );

    expect(result.current.messages.at(-1)?.toolCalls).toEqual([
      {
        callId: "tc_1",
        toolName: "fake.echo",
        status: "pending",
        startedAt: "2026-04-29T10:00:00Z",
      },
    ]);
  });

  it("updates ToolCall status and durationMs on chat.tool_result event", async () => {
    const { result } = renderHook(
      () => useAtlasChat({ session_id: "S7", project_id: "P1", model_id: "m" }),
      { wrapper: createWrapper() },
    );
    await waitFor(() => expect(FakeWS.instances.length).toBe(1));
    const ws = FakeWS.instances[0];
    await waitFor(() => expect(ws.readyState).toBe(1));

    act(() => result.current.send("use the echo tool"));
    act(() =>
      ws.emit({
        type: "chat.tool_use",
        payload: { call_id: "tc_2", tool_name: "fake.echo", started_at: "2026-04-29T10:00:00Z" },
        sequence: 0,
      }),
    );
    act(() =>
      ws.emit({
        type: "chat.tool_result",
        payload: { call_id: "tc_2", ok: true, duration_ms: 42 },
        sequence: 1,
      }),
    );

    expect(result.current.messages.at(-1)?.toolCalls).toEqual([
      {
        callId: "tc_2",
        toolName: "fake.echo",
        status: "ok",
        startedAt: "2026-04-29T10:00:00Z",
        durationMs: 42,
      },
    ]);
  });

  it("logs warning and ignores chat.tool_result with unknown call_id", async () => {
    const warnSpy = vi.spyOn(console, "warn").mockImplementation(() => {});
    const { result } = renderHook(
      () => useAtlasChat({ session_id: "S8", project_id: "P1", model_id: "m" }),
      { wrapper: createWrapper() },
    );
    await waitFor(() => expect(FakeWS.instances.length).toBe(1));
    const ws = FakeWS.instances[0];
    await waitFor(() => expect(ws.readyState).toBe(1));

    act(() => result.current.send("use the echo tool"));
    act(() =>
      ws.emit({
        type: "chat.tool_result",
        payload: { call_id: "unknown_tc", ok: true, duration_ms: 100 },
        sequence: 0,
      }),
    );

    expect(warnSpy).toHaveBeenCalledWith("Received chat.tool_result for unknown call_id: unknown_tc");
    expect(result.current.messages.at(-1)?.toolCalls).toBeUndefined();

    warnSpy.mockRestore();
  });
});
