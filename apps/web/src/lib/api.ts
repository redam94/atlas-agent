export class ApiError extends Error {
  constructor(public readonly status: number, public readonly body: unknown, message: string) {
    super(message);
    this.name = "ApiError";
  }
}

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  const init: RequestInit = {
    method,
    headers: body !== undefined ? { "content-type": "application/json" } : undefined,
    body: body !== undefined ? JSON.stringify(body) : undefined,
  };
  const res = await fetch(path, init);
  if (!res.ok) {
    let parsed: unknown = null;
    try {
      parsed = await res.json();
    } catch {
      // body wasn't JSON; ignore.
    }
    throw new ApiError(res.status, parsed, `${method} ${path} → ${res.status}`);
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

export const api = {
  get: <T>(path: string) => request<T>("GET", path),
  post: <T>(path: string, body?: unknown) => request<T>("POST", path, body),
  patch: <T>(path: string, body?: unknown) => request<T>("PATCH", path, body),
  delete: <T = void>(path: string) => request<T>("DELETE", path),
  postForm: async <T>(path: string, form: FormData): Promise<T> => {
    const res = await fetch(path, { method: "POST", body: form });
    if (!res.ok) {
      let parsed: unknown = null;
      try { parsed = await res.json(); } catch { /* */ }
      throw new ApiError(res.status, parsed, `POST ${path} → ${res.status}`);
    }
    return (await res.json()) as T;
  },
};
