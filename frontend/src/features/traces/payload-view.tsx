import { Link } from "@tanstack/react-router";
import { useState } from "react";

import { CopyButton } from "@/components/copy-button";
import { JsonViewer } from "@/components/json-viewer";
import { ShowAllButton } from "@/components/show-all-button";
import { charactersLabel, TEXT_CLAMP_CHARS } from "@/lib/clamp";
import { useProjectParams } from "@/features/shell/project-context";

import { ChatMessageList } from "./chat-message-list";
import { payloadToClipboardText } from "./chat-messages";
import { classifyPayload, isCutPayload, payloadHint } from "./payload-format";
import { TruncatedBadge } from "./truncated-badge";

interface PayloadViewProps {
  /** "Input" or "Output". */
  label: string;
  value: unknown;
  /** The span's `truncated` flag; the badge goes on whichever payload was cut. */
  spanTruncated: boolean;
}

/** One span payload: chat transcripts render as bubbles, everything else as text or JSON. */
export function PayloadView({ label, value, spanTruncated }: PayloadViewProps) {
  const content = classifyPayload(value);
  const hint = payloadHint(content);

  return (
    <section aria-label={label} className="flex flex-col gap-2.5">
      <div className="flex h-7 min-w-0 items-center gap-2">
        <h3 className="text-overline text-muted-foreground uppercase">{label}</h3>
        {hint ? <span className="text-xs font-medium text-subtle-foreground">{hint}</span> : null}
        {isCutPayload(value, spanTruncated) ? <TruncatedBadge /> : null}
        {content.format !== "empty" ? (
          <CopyButton
            value={payloadToClipboardText(value)}
            label={`Copy ${label.toLowerCase()}`}
            className="ml-auto size-7"
          />
        ) : null}
      </div>
      {content.format === "empty" ? <NoPayloadNote label={label} /> : null}
      {content.format === "chat" ? <ChatMessageList messages={content.messages} /> : null}
      {content.format === "text" ? (
        <div
          role="region"
          aria-label={`${label} text`}
          tabIndex={0}
          className="max-h-[32rem] overflow-auto rounded-input border border-border bg-surface-muted px-3 py-3.5"
        >
          <ClampedPre text={content.text} />
        </div>
      ) : null}
      {content.format === "json" ? (
        <JsonViewer value={content.value} className="rounded-input px-3 py-3.5" />
      ) : null}
    </section>
  );
}

/** Plain text, its first `TEXT_CLAMP_CHARS` until "Show all", so a 1 MiB text stays responsive. */
function ClampedPre({ text }: { text: string }) {
  const [expanded, setExpanded] = useState(false);
  const isLong = text.length > TEXT_CLAMP_CHARS;
  return (
    <>
      <pre className="text-label leading-normal break-words whitespace-pre-wrap text-foreground">
        {isLong && !expanded ? `${text.slice(0, TEXT_CLAMP_CHARS)}…` : text}
      </pre>
      {isLong ? (
        <ShowAllButton
          expanded={expanded}
          onToggle={() => {
            setExpanded((current) => !current);
          }}
          label={charactersLabel(text.length)}
          className="mt-2"
        />
      ) : null}
    </>
  );
}

function NoPayloadNote({ label }: { label: string }) {
  const { orgId, projectId } = useProjectParams();
  return (
    <div className="rounded-tile border border-dashed border-border px-3.5 py-3 text-xs text-muted-foreground">
      <p className="font-medium text-foreground">No {label.toLowerCase()} captured</p>
      <p className="mt-0.5">
        The SDK didn&apos;t send one, or payload capture is off in{" "}
        <Link
          to="/$orgId/$projectId/settings/project"
          params={{ orgId, projectId }}
          className="rounded-sm text-accent underline underline-offset-2"
        >
          project settings
        </Link>
        .
      </p>
    </div>
  );
}
