import type { ReactNode } from "react";

import { UnknownValue } from "@/components/unknown-value";
import type { AuditEvent } from "@/lib/api";
import { cn } from "@/lib/utils";

import {
  auditActorName,
  auditActorVia,
  shortTargetId,
  targetTypeLabel,
  type AuditChange,
} from "./audit-actions";
import { InitialAvatar } from "./initial-avatar";

interface DetailRowProps {
  label: string;
  children: ReactNode;
  className?: string;
}

export function DetailRow({ label, children, className }: DetailRowProps) {
  return (
    <div className={cn("contents", className)}>
      <dt className="text-muted-foreground">{label}</dt>
      <dd className="min-w-0">{children}</dd>
    </div>
  );
}

export function AuditActor({ event }: { event: AuditEvent }) {
  const name = auditActorName(event);
  const via = auditActorVia(event);
  const person = event.actor !== null || event.actor_email !== null;
  return (
    <span className="flex min-w-0 items-center gap-2">
      <InitialAvatar
        name={name}
        seed={event.actor?.id ?? event.actor_email ?? "system"}
        size="sm"
        tone={person ? undefined : "neutral"}
      />
      <span className="flex min-w-0 flex-col">
        <span
          title={event.actor?.email ?? event.actor_email ?? undefined}
          className={cn(
            "truncate text-sm font-medium",
            person ? "text-foreground" : "text-muted-foreground",
          )}
        >
          {name}
        </span>
        {via ? (
          <span className="truncate text-xs font-medium text-muted-foreground">{via}</span>
        ) : null}
      </span>
    </span>
  );
}

function changeValue(value: unknown): string {
  return typeof value === "string" ? value : JSON.stringify(value);
}

/** Figma "Audit log v3" Changes: one line per field, the old value muted, the new one strong. */
export function AuditChanges({ changes }: { changes: AuditChange[] }) {
  return (
    <div className="flex flex-col gap-1.5 rounded-lg bg-surface px-3 py-2.5">
      <span className="text-xs font-semibold text-muted-foreground">Changes</span>
      <ul className="flex flex-col gap-1.5">
        {changes.map((change) => (
          <li
            key={change.field}
            className="flex min-w-0 flex-wrap items-baseline gap-x-2 font-mono text-label"
          >
            <span className="w-28 shrink-0 text-muted-foreground">{change.field}</span>
            <span className="break-all text-muted-foreground">{changeValue(change.from)}</span>
            <span aria-hidden className="font-sans text-subtle-foreground">
              →
            </span>
            <span className="sr-only">changed to</span>
            <span className="break-all text-foreground">{changeValue(change.to)}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

export function AuditTarget({ event }: { event: AuditEvent }) {
  if (!event.target_type && !event.target_id) {
    return <UnknownValue reason="No target recorded" />;
  }
  return (
    <span className="flex min-w-0 items-center gap-1.5 whitespace-nowrap">
      {event.target_type ? (
        <span className="text-xs font-medium text-muted-foreground">
          {targetTypeLabel(event.target_type)}
        </span>
      ) : null}
      {event.target_id ? (
        <span title={event.target_id} className="truncate font-mono text-label text-foreground">
          {shortTargetId(event.target_id)}
        </span>
      ) : null}
    </span>
  );
}

export function AuditIp({ event }: { event: AuditEvent }) {
  if (!event.ip) {
    return <UnknownValue reason="Not recorded" className="font-mono text-label" />;
  }
  return <span className="truncate font-mono text-label text-muted-foreground">{event.ip}</span>;
}
