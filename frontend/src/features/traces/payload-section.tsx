import { Link } from "@tanstack/react-router";
import { useMemo, type ReactNode } from "react";

import { CopyButton } from "@/components/copy-button";
import { useProjectParams, useProjectQuery } from "@/features/shell/project-context";

import { ChatMessageList } from "./chat-message-list";
import { payloadToClipboardText } from "./chat-messages";
import { GuardedPayload } from "./guarded-payload";
import { classifyPayload, isCutPayload, payloadHint } from "./payload-format";
import { TruncatedBadge } from "./truncated-badge";

export type PayloadLabel = "Input" | "Output";

export function PayloadTitle({ label, children }: { label: PayloadLabel; children?: ReactNode }) {
  return (
    <div className="flex h-7 min-w-0 items-center gap-2">
      <h3 className="text-overline text-muted-foreground uppercase">{label}</h3>
      {children}
    </div>
  );
}

interface PayloadSectionProps {
  label: PayloadLabel;
  value: unknown;
  spanTruncated: boolean;
  /** The empty note may say the children have none too. */
  childrenChecked: boolean;
  /** Where the payload came from, when it isn't the span's own. */
  source?: ReactNode;
}

/** A payload's title row (hint, cut badge, Copy), its source note, then the payload itself. */
export function PayloadSection({
  label,
  value,
  spanTruncated,
  childrenChecked,
  source,
}: PayloadSectionProps) {
  // Parsing and serializing a 1 MiB payload is not free: once per payload, not per render.
  const content = useMemo(() => classifyPayload(value), [value]);
  const serialized = useMemo(() => payloadToClipboardText(value), [value]);
  const hint = payloadHint(content);

  return (
    <section aria-label={label} className="flex flex-col gap-2.5">
      <PayloadTitle label={label}>
        {hint ? <span className="text-xs font-medium text-subtle-foreground">{hint}</span> : null}
        {isCutPayload(value, spanTruncated) ? <TruncatedBadge /> : null}
        {content.format !== "empty" ? (
          <CopyButton
            value={serialized}
            label={`Copy ${label.toLowerCase()}`}
            className="ml-auto size-7"
          />
        ) : null}
      </PayloadTitle>
      {source}
      {content.format === "empty" ? (
        <NoPayloadNote label={label} childrenChecked={childrenChecked} />
      ) : null}
      {content.format === "chat" ? <ChatMessageList messages={content.messages} /> : null}
      {content.format === "text" || content.format === "json" ? (
        <GuardedPayload label={label} value={value} serialized={serialized} />
      ) : null}
    </section>
  );
}

function NoPayloadNote({
  label,
  childrenChecked,
}: {
  label: PayloadLabel;
  childrenChecked: boolean;
}) {
  const project = useProjectQuery();
  const noun = label.toLowerCase();
  // The capture wording only when capture really is off; unknown or on, the span simply has none.
  if (project.data?.capture_payloads !== false) {
    return (
      <p className="rounded-tile border border-border px-3.5 py-3 text-xs text-muted-foreground">
        {childrenChecked
          ? `This span has no ${noun}. Its children don't either.`
          : `This span has no ${noun}.`}
      </p>
    );
  }
  return <CaptureOffNote noun={noun} />;
}

function CaptureOffNote({ noun }: { noun: string }) {
  const { orgId, projectId } = useProjectParams();
  return (
    <div className="rounded-tile border border-dashed border-border px-3.5 py-3 text-xs text-muted-foreground">
      <p className="font-medium text-foreground">No {noun} captured</p>
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
