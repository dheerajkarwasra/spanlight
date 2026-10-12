import { CircleAlert, Plus, Trash2 } from "lucide-react";
import { useId } from "react";

import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/input";
import type { ChatMessage, ChatRole } from "@/lib/api";

const ROLES: ChatRole[] = ["system", "user", "assistant"];

interface MessagesFieldsProps {
  messages: ChatMessage[];
  error: string | undefined;
  onChange: (messages: ChatMessage[]) => void;
}

/** Typed messages of an ad-hoc target (Figma "Experiments — Create — ad-hoc messages"). */
export function MessagesFields({ messages, error, onChange }: MessagesFieldsProps) {
  const id = useId();
  const errorId = `${id}-error`;

  function patch(index: number, change: Partial<ChatMessage>) {
    onChange(messages.map((message, at) => (at === index ? { ...message, ...change } : message)));
  }

  return (
    <fieldset className="flex flex-col gap-2" aria-describedby={error ? errorId : undefined}>
      <legend className="mb-2 text-label font-semibold text-foreground">Messages</legend>
      {messages.map((message, index) => (
        <div key={index} className="flex flex-col gap-1.5 rounded-tile bg-surface-muted p-3.5">
          <div className="flex items-center justify-between gap-2">
            <label htmlFor={`${id}-role-${index}`} className="sr-only">
              {`Role of message ${index + 1}`}
            </label>
            <select
              id={`${id}-role-${index}`}
              value={message.role}
              onChange={(event) => patch(index, { role: event.target.value as ChatRole })}
              className="rounded-full border border-border-strong bg-surface px-2 py-0.5 font-mono text-[11px] text-foreground"
            >
              {ROLES.map((role) => (
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
                onClick={() => onChange(messages.filter((_, at) => at !== index))}
              >
                <Trash2 aria-hidden />
              </Button>
            ) : null}
          </div>
          <Textarea
            aria-label={`Text of message ${index + 1}`}
            rows={2}
            value={message.content}
            onChange={(event) => patch(index, { content: event.target.value })}
            className="field-sizing-content max-h-64 min-h-[3.25rem] border-transparent bg-transparent px-0 py-0 font-mono text-xs"
          />
        </div>
      ))}
      <Button
        variant="ghost"
        size="sm"
        className="self-start"
        onClick={() =>
          onChange([
            ...messages,
            { role: messages.at(-1)?.role === "user" ? "assistant" : "user", content: "" },
          ])
        }
      >
        <Plus aria-hidden />
        Add message
      </Button>
      {error ? (
        <p id={errorId} className="flex items-start gap-1.5 text-xs font-medium text-danger-text">
          <CircleAlert aria-hidden className="mt-px size-3.5 shrink-0" strokeWidth={2.25} />
          {error}
        </p>
      ) : (
        <p className="text-xs font-medium text-muted-foreground">
          These messages are sent unchanged for every item; the item input is not used. Pick a
          prompt version to fill variables from each item.
        </p>
      )}
    </fieldset>
  );
}
