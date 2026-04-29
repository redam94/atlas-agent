import { useEffect, useImperativeHandle, useState, forwardRef } from "react";
import tippy, { type Instance } from "tippy.js";
import { ReactRenderer } from "@tiptap/react";
import type { Entity } from "@/lib/api/entities";

interface MentionListProps {
  items: Entity[];
  command: (item: { id: string; label: string }) => void;
}

// eslint-disable-next-line react-refresh/only-export-components
const MentionList = forwardRef<{ onKeyDown: (e: KeyboardEvent) => boolean }, MentionListProps>(
  ({ items, command }, ref) => {
    const [active, setActive] = useState(0);
    useEffect(() => setActive(0), [items]);

    useImperativeHandle(ref, () => ({
      onKeyDown: (event: KeyboardEvent) => {
        if (event.key === "ArrowUp") {
          setActive((i) => (i + items.length - 1) % items.length);
          return true;
        }
        if (event.key === "ArrowDown") {
          setActive((i) => (i + 1) % items.length);
          return true;
        }
        if (event.key === "Enter") {
          const item = items[active];
          if (item) command({ id: item.id, label: item.name });
          return true;
        }
        return false;
      },
    }));

    if (items.length === 0) {
      return (
        <div className="rounded-md border bg-popover p-2 text-xs text-muted-foreground shadow-md">
          No matching entities
        </div>
      );
    }

    return (
      <div className="rounded-md border bg-popover p-1 shadow-md">
        {items.map((it, i) => (
          <button
            key={it.id}
            type="button"
            onClick={() => command({ id: it.id, label: it.name })}
            className={`flex w-full items-center justify-between rounded px-2 py-1 text-left text-sm ${
              i === active ? "bg-accent" : ""
            }`}
          >
            <span className="truncate">{it.name}</span>
            {it.entity_type && (
              <span className="ml-2 text-xs text-muted-foreground">
                {it.entity_type}
              </span>
            )}
          </button>
        ))}
      </div>
    );
  },
);
MentionList.displayName = "MentionList";

export function renderMentionSuggestion() {
  let component: ReactRenderer<{ onKeyDown: (e: KeyboardEvent) => boolean }> | null = null;
  let popup: Instance[] | null = null;

  return {
    onStart: (props: Record<string, unknown>) => {
      const { editor, clientRect } = props as never;
      component = new ReactRenderer(MentionList, { props: props as never, editor });
      if (!clientRect) return;
      popup = tippy("body", {
        getReferenceClientRect: clientRect as (() => DOMRect) | null,
        appendTo: () => document.body,
        content: component.element,
        showOnCreate: true,
        interactive: true,
        trigger: "manual",
        placement: "bottom-start",
      });
    },
    onUpdate(props: Record<string, unknown>) {
      component?.updateProps(props);
      const { clientRect } = props as never;
      if (clientRect && popup?.[0]) {
        popup[0].setProps({ getReferenceClientRect: clientRect as (() => DOMRect) | null });
      }
    },
    onKeyDown(props: Record<string, unknown>) {
      const { event } = props as never;
      if ((event as KeyboardEvent).key === "Escape") {
        popup?.[0]?.hide();
        return true;
      }
      return component?.ref?.onKeyDown(event as KeyboardEvent) ?? false;
    },
    onExit() {
      popup?.[0]?.destroy();
      component?.destroy();
    },
  };
}
