import { CircleAlert, CircleCheck, Clock, RotateCcw, type LucideIcon } from "lucide-react";

import type { ChannelTestResult, Delivery } from "@/lib/api";
import { formatRelativeTime } from "@/lib/format";

/** The outbox gives up after this many attempts (`notifications/outbox.py` `MAX_ATTEMPTS`). */
export const MAX_ATTEMPTS = 8;

export type StatusTone = "success" | "warning" | "danger" | "neutral";

export interface DeliveryStatusView {
  label: string;
  tone: StatusTone;
  icon: LucideIcon;
}

/**
 * Figma "Alerts/Status pill": Sent, Retrying, Failed, with an icon as well as a colour. A pending
 * row that has not been tried yet is "Queued": calling it "Retrying" would claim a failure.
 */
export function deliveryStatus(
  delivery: Pick<Delivery, "status" | "attempts">,
): DeliveryStatusView {
  switch (delivery.status) {
    case "sent":
      return { label: "Sent", tone: "success", icon: CircleCheck };
    case "failed":
      return { label: "Failed", tone: "danger", icon: CircleAlert };
    case "pending":
      return delivery.attempts > 0
        ? { label: "Retrying", tone: "warning", icon: RotateCcw }
        : { label: "Queued", tone: "neutral", icon: Clock };
  }
}

const EVENT_LABELS: Record<string, string> = {
  "alert.fired": "Fired",
  "alert.resolved": "Resolved",
  "budget.exceeded": "Exceeded",
  "insight.opened": "Insight opened",
  test: "Test",
};

/** The Doctor insight an `insight.opened` row announced, with its project; null otherwise. */
export function deliveryInsightTarget(
  delivery: Pick<Delivery, "summary">,
): { insightId: string; projectId: string } | null {
  const summary = delivery.summary;
  if (summary?.event !== "insight.opened" || !summary.insight_id || !summary.project_id) {
    return null;
  }
  return { insightId: summary.insight_id, projectId: summary.project_id };
}

/** The event badge: Fired, Resolved, Exceeded, Insight opened or Test. Null for a row without a summary. */
export function deliveryEventLabel(delivery: Pick<Delivery, "summary">): string | null {
  const event = delivery.summary?.event;
  if (!event) {
    return null;
  }
  return EVENT_LABELS[event] ?? event;
}

/**
 * The row title: the rule's name, the insight's title, "Test notification", or null when the row
 * has no summary.
 */
export function deliveryTitle(delivery: Pick<Delivery, "summary">): string | null {
  const summary = delivery.summary;
  if (!summary) {
    return null;
  }
  if (summary.event === "test") {
    return "Test notification";
  }
  if (summary.event === "insight.opened") {
    return summary.title ?? "Insight opened";
  }
  return summary.rule_name ?? null;
}

/**
 * "3 of 8"; a failed row that cannot be retried (an email that was never rendered) shows only
 * its attempts, since no more will come.
 */
export function attemptsLabel(
  delivery: Pick<Delivery, "status" | "attempts" | "retryable">,
): string {
  if (delivery.status === "failed" && !delivery.retryable) {
    return `${delivery.attempts}`;
  }
  return `${delivery.attempts} of ${MAX_ATTEMPTS}`;
}

/**
 * When the worker tries a pending row next ("in 4 min", "now"). Null for a settled row, which has
 * no next attempt.
 */
export function nextAttemptLabel(
  delivery: Pick<Delivery, "status" | "next_attempt_at">,
  now: Date = new Date(),
): string | null {
  if (delivery.status !== "pending" || delivery.next_attempt_at === null) {
    return null;
  }
  if (new Date(delivery.next_attempt_at).getTime() <= now.getTime()) {
    return "now";
  }
  return formatRelativeTime(delivery.next_attempt_at, now);
}

/** Only a failed row the server would take back can be retried (`409 NOT_RETRYABLE` otherwise). */
export function canRetry(delivery: Pick<Delivery, "status" | "retryable">): boolean {
  return delivery.status === "failed" && delivery.retryable;
}

export interface TestResultView {
  tone: "success" | "warning" | "danger";
  message: string;
}

/** The §11.1 test-send copy: "Test sent", "Test failed: {error}", "Queued for retry: {error}". */
export function testResultView(result: ChannelTestResult): TestResultView {
  const error = result.error ?? "no details";
  switch (result.status) {
    case "sent":
      return { tone: "success", message: "Test sent" };
    case "failed":
      return { tone: "danger", message: `Test failed: ${error}` };
    case "pending":
      return { tone: "warning", message: `Queued for retry: ${error}` };
  }
}

const shortTimeFormat = new Intl.DateTimeFormat("en-US", {
  month: "short",
  day: "numeric",
  hour: "2-digit",
  minute: "2-digit",
  hourCycle: "h23",
});

/** "Oct 10, 09:12", local time, 24 h. Null for an unreadable timestamp. */
export function shortTime(iso: string): string | null {
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? null : shortTimeFormat.format(date);
}
