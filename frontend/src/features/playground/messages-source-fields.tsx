import { Plus, Trash2 } from "lucide-react";
import { useRef } from "react";

import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/input";
import type { ChatMessage, ChatRole } from "@/lib/api";
import { MAX_CHAT_MESSAGES } from "@/lib/prompt-template";
import { useListKeys } from "@/lib/use-list-keys";

import type { ColumnState } from "./config-state";

const ROLE_CYCLE: ChatRole[] = ["system", "user", "assistant"];

interface MessagesSourceFieldsProps {
  column: ColumnState;
  onChange: (patch: Partial<ColumnState>) => void;
}

/** After a removal, focus the message now at `index` (or the new last one). */
function focusMessage(list: HTMLElement | null, index: number) {
  window.setTimeout(() => {
    const fields = list?.querySelectorAll("textarea");
    fields?.[Math.min(index, fields.length - 1)]?.focus();
  });
}

function nextRole(messages: readonly ChatMessage[]): ChatRole {
  const last = messages.at(-1)?.role;
  return last === "user" ? "assistant" : "user";
}

/** Messages written in place (Figma "Playground — Messages source"): a role tag and mono text. */
export function MessagesSourceFields({ column, onChange }: MessagesSourceFieldsProps) {
  const messages = column.messages;
  const { keys, added, removed } = useListKeys(messages.length);
  const listRef = useRef<HTMLDivElement>(null);

  function patchMessage(index: number, patch: Partial<ChatMessage>) {
    onChange({
      messages: messages.map((message, at) => (at === index ? { ...message, ...patch } : message)),
    });
  }

  return (
    <div ref={listRef} className="flex flex-col gap-2">
      {messages.map((message, index) => (
        <div key={keys[index]} className="flex flex-col gap-1.5 rounded-tile bg-surface-muted p-3">
          <div className="flex items-center justify-between gap-2">
            <label
              htmlFor={`${column.key}-role-${index}`}
              className="sr-only"
            >{`Role of message ${index + 1}`}</label>
            <select
              id={`${column.key}-role-${index}`}
              value={message.role}
              onChange={(event) => patchMessage(index, { role: event.target.value as ChatRole })}
              className="rounded-sm bg-transparent text-overline text-subtle-foreground uppercase"
            >
              {ROLE_CYCLE.map((role) => (
                <option key={role} value={role}>
                  {role}
                </option>
              ))}
            </select>
            {messages.length > 1 ? (
              <Button
                variant="ghost"
                size="icon-sm"
                aria-label={`Remove message ${index + 1}`}
                onClick={() => {
                  removed(index);
                  onChange({ messages: messages.filter((_, at) => at !== index) });
                  focusMessage(listRef.current, index);
                }}
              >
                <Trash2 aria-hidden />
              </Button>
            ) : null}
          </div>
          <Textarea
            aria-label={`Text of message ${index + 1}`}
            rows={2}
            value={message.content}
            onChange={(event) => patchMessage(index, { content: event.target.value })}
            className="field-sizing-content max-h-64 min-h-[3.25rem] border-transparent bg-transparent px-0 py-0 font-mono text-xs"
          />
        </div>
      ))}
      <Button
        variant="secondary"
        size="sm"
        className="self-start"
        disabled={messages.length >= MAX_CHAT_MESSAGES}
        onClick={() => {
          added();
          onChange({ messages: [...messages, { role: nextRole(messages), content: "" }] });
        }}
      >
        <Plus aria-hidden />
        Add message
      </Button>
    </div>
  );
}
