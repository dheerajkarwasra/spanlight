import { Plus, Trash2 } from "lucide-react";
import { useRef } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import type { ChatMessage, ChatRole, PromptTemplate } from "@/lib/api";
import { useListKeys } from "@/lib/use-list-keys";
import { cn } from "@/lib/utils";

import { CHAT_ROLES, editedMessages } from "./draft";
import { MAX_CHAT_MESSAGES, type InvalidTemplateError } from "@/lib/prompt-template";

const EDITOR = "min-h-32 font-mono text-[13px] leading-5";

interface TemplateEditorProps {
  template: PromptTemplate;
  onChange: (template: PromptTemplate) => void;
  /** The version being edited, for the "Edited" marks; null for a new prompt. */
  base: PromptTemplate | null;
  problem: InvalidTemplateError | null;
  /** The id of the line that explains `problem`, for aria-describedby. */
  problemId: string;
}

/** Figma "Prompts — Editor": a plain-text area, or one card per chat message. */
export function TemplateEditor(props: TemplateEditorProps) {
  const { template, onChange, problem, problemId } = props;
  if (template.kind === "text") {
    return (
      <Textarea
        aria-label="Template"
        value={template.text}
        rows={8}
        spellCheck={false}
        aria-invalid={problem !== null || undefined}
        aria-describedby={problem ? problemId : undefined}
        className={EDITOR}
        onChange={(event) => onChange({ kind: "text", text: event.target.value })}
      />
    );
  }
  return <ChatEditor {...props} messages={template.messages} />;
}

function nextRole(messages: readonly ChatMessage[]): ChatRole {
  return messages.at(-1)?.role === "user" ? "assistant" : "user";
}

function ChatEditor({
  messages,
  onChange,
  base,
  problem,
  problemId,
}: TemplateEditorProps & { messages: ChatMessage[] }) {
  const edited = editedMessages({ kind: "chat", messages }, base);
  const { keys, added, removed } = useListKeys(messages.length);
  const listRef = useRef<HTMLOListElement>(null);
  const update = (next: ChatMessage[]) => onChange({ kind: "chat", messages: next });

  function remove(index: number) {
    removed(index);
    update(messages.filter((_, at) => at !== index));
    // The removed row took the focused button with it: land on the message now in its place.
    window.setTimeout(() => {
      const fields = listRef.current?.querySelectorAll("textarea");
      fields?.[Math.min(index, fields.length - 1)]?.focus();
    });
  }

  return (
    <div className="flex flex-col gap-3">
      <ol ref={listRef} className="flex flex-col gap-3" aria-label="Messages">
        {messages.map((message, index) => (
          <li key={keys[index]}>
            <MessageEditor
              message={message}
              index={index}
              edited={edited[index] === true}
              canRemove={messages.length > 1}
              invalid={problem?.position?.message === index}
              problemId={problemId}
              onChange={(next) => update(messages.map((old, at) => (at === index ? next : old)))}
              onRemove={() => remove(index)}
            />
          </li>
        ))}
      </ol>
      <Button
        size="sm"
        className="self-start border-border-strong bg-transparent shadow-none"
        disabled={messages.length >= MAX_CHAT_MESSAGES}
        onClick={() => {
          added();
          update([...messages, { role: nextRole(messages), content: "" }]);
        }}
      >
        <Plus aria-hidden />
        Add message
      </Button>
    </div>
  );
}

interface MessageEditorProps {
  message: ChatMessage;
  index: number;
  edited: boolean;
  canRemove: boolean;
  invalid: boolean;
  problemId: string;
  onChange: (message: ChatMessage) => void;
  onRemove: () => void;
}

function MessageEditor({ message, index, edited, ...props }: MessageEditorProps) {
  const position = `message ${String(index + 1)}`;
  return (
    <div className="flex flex-col gap-2.5 rounded-tile bg-surface-muted p-3 sm:p-4">
      <div className="flex items-center gap-2.5">
        <Select
          value={message.role}
          onValueChange={(role) => props.onChange({ ...message, role: role as ChatRole })}
        >
          <SelectTrigger
            aria-label={`Role of ${position}`}
            className="h-8 w-auto rounded-full border-border-strong px-3 text-[13px] font-semibold"
          >
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {CHAT_ROLES.map((role) => (
              <SelectItem key={role} value={role}>
                {role}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        {edited ? (
          <Badge variant="warning" size="sm">
            Edited
          </Badge>
        ) : null}
        <Button
          variant="ghost"
          size="icon-sm"
          className={cn("ml-auto", !props.canRemove && "invisible")}
          aria-label={`Remove ${position}`}
          disabled={!props.canRemove}
          onClick={props.onRemove}
        >
          <Trash2 aria-hidden />
        </Button>
      </div>
      <Textarea
        aria-label={`Content of ${position}`}
        value={message.content}
        rows={4}
        spellCheck={false}
        aria-invalid={props.invalid || undefined}
        aria-describedby={props.invalid ? props.problemId : undefined}
        className={EDITOR}
        onChange={(event) => props.onChange({ ...message, content: event.target.value })}
      />
    </div>
  );
}
