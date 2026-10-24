import { Image as ImageIcon, Wrench } from "lucide-react";
import { useState } from "react";

import { CopyButton } from "@/components/copy-button";
import { JsonViewer } from "@/components/json-viewer";
import { ShowAllButton } from "@/components/show-all-button";
import { charactersLabel } from "@/lib/clamp";
import { cn } from "@/lib/utils";

import {
  messageText,
  payloadToClipboardText,
  ROLE_LABELS,
  type ChatMessage,
  type ChatPart,
  type ChatRole,
} from "./chat-messages";
import { ReasoningCard, RedactedReasoningNote } from "./reasoning-card";

/** Long texts (system prompts, retrieved context) start clamped to keep the panel scannable. */
const CLAMP_CHARS = 1200;

/**
 * Figma "Traces/Chat bubble". System prompts are centred and dashed, the user speaks from the
 * right in an ink bubble, the assistant (and tools) answer from the left on surface-muted.
 * The user bubble uses the always-dark hero surface so it stays ink-coloured in the dark theme.
 */
const BUBBLE_STYLES: Record<ChatRole, { row: string; bubble: string; label: string }> = {
  system: {
    row: "justify-center",
    bubble:
      "w-full max-w-[88%] items-center rounded-tile border border-dashed border-border py-2.5 text-center",
    label: "text-muted-foreground",
  },
  user: {
    row: "justify-end",
    bubble:
      "max-w-[85%] rounded-tile rounded-br-md bg-hero-card py-3 text-hero-card-foreground dark:border dark:border-border",
    label: "text-rail-muted-foreground",
  },
  assistant: {
    row: "justify-start",
    bubble: "max-w-[85%] rounded-tile rounded-bl-md border border-border bg-surface-muted py-3",
    label: "text-muted-foreground",
  },
  tool: {
    row: "justify-start",
    bubble: "max-w-[85%] rounded-tile rounded-bl-md border border-border bg-surface py-3",
    label: "text-muted-foreground",
  },
};

export function ChatMessageList({ messages }: { messages: ChatMessage[] }) {
  return (
    <ol className="flex flex-col gap-2.5">
      {messages.map((message, index) => (
        <li key={index} className={cn("flex", BUBBLE_STYLES[message.role].row)}>
          <ChatBubble message={message} />
        </li>
      ))}
    </ol>
  );
}

function ChatBubble({ message }: { message: ChatMessage }) {
  const style = BUBBLE_STYLES[message.role];
  const isSystem = message.role === "system";
  const isUser = message.role === "user";
  const roleLabel = ROLE_LABELS[message.role];
  const copyValue = messageText(message) ?? payloadToClipboardText(message.parts);

  return (
    <article
      aria-label={`${roleLabel} message`}
      className={cn("flex min-w-0 flex-col gap-1.5 px-3.5", style.bubble)}
    >
      <header
        className={cn("flex w-full min-w-0 items-center gap-1.5", isSystem && "justify-center")}
      >
        <span className={cn("shrink-0 text-overline uppercase", style.label)}>{roleLabel}</span>
        {message.name ? (
          <span className={cn("truncate font-mono text-xs", style.label)}>{message.name}</span>
        ) : null}
        {message.toolCallId ? (
          <span
            title={message.toolCallId}
            className={cn("truncate font-mono text-xs", style.label)}
          >
            {message.toolCallId}
          </span>
        ) : null}
        {isSystem ? null : <span aria-hidden className="flex-1" />}
        <CopyButton
          value={copyValue}
          label={`Copy ${roleLabel.toLowerCase()} message`}
          tone={isUser ? "ink" : "default"}
          className={cn("-my-1 shrink-0", isSystem ? "size-6" : "size-7")}
        />
      </header>
      <div
        className={cn(
          "flex min-w-0 flex-col gap-2",
          isSystem ? "text-xs font-medium text-muted-foreground" : "text-sm",
        )}
      >
        {message.parts.length === 0 ? (
          <p className="text-xs italic opacity-80">Empty message</p>
        ) : (
          message.parts.map((part, index) => (
            <ChatPartView key={index} part={part} onInk={isUser} />
          ))
        )}
      </div>
    </article>
  );
}

function ChatPartView({ part, onInk }: { part: ChatPart; onInk: boolean }) {
  // JSON keeps its own light surface so the syntax colours stay readable inside the ink bubble.
  const jsonClass = "bg-surface p-2 text-foreground";
  switch (part.type) {
    case "text":
      return <ClampedText text={part.text} onInk={onInk} />;
    case "image":
      return part.src ? (
        <ImageThumbnail src={part.src} description={part.description} onInk={onInk} />
      ) : (
        <ImageLabel description={part.description} onInk={onInk} />
      );
    case "tool_call":
      return (
        <div className="flex flex-col gap-1">
          <ToolLabel title="Tool call" name={part.name} id={part.id} onInk={onInk} />
          <JsonViewer value={part.arguments} defaultExpandDepth={2} className={jsonClass} />
        </div>
      );
    case "tool_result":
      return (
        <div className="flex flex-col gap-1">
          <ToolLabel
            title={part.isError ? "Tool error" : "Tool result"}
            id={part.toolCallId}
            danger={part.isError}
            onInk={onInk}
          />
          {typeof part.content === "string" ? (
            <ClampedText text={part.content} onInk={onInk} />
          ) : (
            <JsonViewer value={part.content} className={jsonClass} />
          )}
        </div>
      );
    case "thinking":
      return (
        <ReasoningCard>
          <ClampedText text={part.text} onInk={false} />
        </ReasoningCard>
      );
    case "redacted_thinking":
      return <RedactedReasoningNote />;
    case "json":
      return <JsonViewer value={part.value} className={jsonClass} />;
  }
}

interface ImageProps {
  description: string;
  onInk: boolean;
}

function ImageLabel({ description, onInk }: ImageProps) {
  return (
    <span
      title={description}
      className={cn(
        "inline-flex max-w-full items-center gap-1.5 self-start rounded-md border px-2 py-1 text-xs",
        onInk
          ? "border-rail-tile text-rail-muted-foreground"
          : "border-border bg-surface text-muted-foreground",
      )}
    >
      <ImageIcon aria-hidden className="size-3.5 shrink-0" />
      <span className="truncate">{description}</span>
    </span>
  );
}

/**
 * An inline `data:` image as a lazy thumbnail (the CSP allows `img-src data:`). A payload cut by
 * ingestion leaves broken base64, so a failed load falls back to the label.
 */
function ImageThumbnail({ src, description, onInk }: ImageProps & { src: string }) {
  const [failed, setFailed] = useState(false);
  if (failed) {
    return <ImageLabel description={description} onInk={onInk} />;
  }
  return (
    <img
      src={src}
      alt={description}
      title={description}
      loading="lazy"
      onError={() => {
        setFailed(true);
      }}
      className="max-h-40 max-w-40 self-start rounded-md border border-border bg-surface object-contain"
    />
  );
}

interface ToolLabelProps {
  title: string;
  name?: string;
  id: string | null;
  danger?: boolean;
  /** Inside the ink user bubble (Anthropic tool results arrive as user messages). */
  onInk: boolean;
}

function ToolLabel({ title, name, id, danger = false, onInk }: ToolLabelProps) {
  const strong = onInk ? "text-hero-card-foreground" : "text-foreground";
  const muted = onInk ? "text-rail-muted-foreground" : "text-muted-foreground";
  const dangerText = onInk ? "text-rail-danger" : "text-danger-text";
  return (
    <div className="flex min-w-0 items-center gap-1.5 text-xs">
      <Wrench
        aria-hidden
        className={cn("size-3.5 shrink-0", danger ? "text-danger" : "text-kind-tool")}
      />
      <span className={cn("font-medium", danger ? dangerText : strong)}>{title}</span>
      {name ? <span className={cn("font-mono", strong)}>{name}</span> : null}
      {id ? (
        <span title={id} className={cn("truncate font-mono", muted)}>
          {id}
        </span>
      ) : null}
    </div>
  );
}

function ClampedText({ text, onInk }: { text: string; onInk: boolean }) {
  const [expanded, setExpanded] = useState(false);
  const isLong = text.length > CLAMP_CHARS;
  const shown = isLong && !expanded ? `${text.slice(0, CLAMP_CHARS).trimEnd()}…` : text;

  return (
    <div className="flex flex-col items-start gap-1">
      <p className="w-full break-words whitespace-pre-wrap">{shown}</p>
      {isLong ? (
        <ShowAllButton
          expanded={expanded}
          onToggle={() => {
            setExpanded((current) => !current);
          }}
          label={charactersLabel(text.length)}
          className={cn(onInk && "text-lime")}
        />
      ) : null}
    </div>
  );
}
