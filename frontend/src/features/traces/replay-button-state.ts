import type { ReplaySource } from "@/lib/api";

/** What the span panel's Replay button shows, derived from the span's `replay-source` read. */
export interface ReplayButtonState {
  label: string;
  disabled: boolean;
  /** Why the button is disabled, or what to know before using it (a not-exact replay). */
  reason: string | null;
  /** Offer "Edit and replay": the stored request can be sent once its marked parts are edited. */
  edit: boolean;
  /** Where the button (or "Edit and replay") leads; null when it leads nowhere. */
  href: "playground" | null;
}

export const REPLAY_LABEL = "Replay";
export const NOT_EXACT_LABEL = "Open in playground (not exact)";
export const EDIT_AND_REPLAY_LABEL = "Edit and replay";
export const VIEWER_REASON = "Replay needs the member role.";
export const REPLAY_OFF_REASON = "Replay is turned off on this server.";
export const NOT_EXACT_REASON =
  "Recorded through OpenTelemetry: the messages only, without tools or parameters.";

/** Refusals the user can fix by editing the stored request in the playground's replay column. */
const EDITABLE_REFUSALS = new Set(["REPLAY_PAYLOAD_REDACTED", "REPLAY_PAYLOAD_TRUNCATED"]);

/** Used only when the server's problem detail is empty. */
const REFUSAL_FALLBACKS: Record<string, string> = {
  REPLAY_PAYLOAD_REDACTED:
    "The stored request has redacted parts ([REDACTED] or [MAX_DEPTH]). Remove or replace them before replaying.",
  REPLAY_PAYLOAD_TRUNCATED: "The stored request was cut at 32 KB, so it cannot be sent as it is.",
  REPLAY_NO_PAYLOAD:
    "This span was stored without its request: payload capture was off, or retention removed it.",
  REPLAY_UNSUPPORTED_INPUT:
    "This span's input is not a provider request, so there is nothing to send.",
  REPLAY_UNPRICED_OPTION:
    "The request uses a priority tier or a provider-run tool, which the replay budget cannot price.",
};
const UNSUPPORTED_FALLBACK = REFUSAL_FALLBACKS.REPLAY_UNSUPPORTED_INPUT ?? "";

function disabled(reason: string | null): ReplayButtonState {
  return { label: REPLAY_LABEL, disabled: true, reason, edit: false, href: null };
}

/**
 * The button's state. The server-wide switch wins, then the role, then the span itself; while
 * `source` is still loading the button is disabled without a reason.
 */
export function replayButtonState(
  source: ReplaySource | undefined,
  canRun: boolean,
  replayOff: boolean,
): ReplayButtonState {
  if (replayOff) {
    return disabled(REPLAY_OFF_REASON);
  }
  if (!canRun) {
    return disabled(VIEWER_REASON);
  }
  if (source === undefined) {
    return disabled(null);
  }
  if (source.refusal !== null || source.fidelity === null) {
    const code = source.refusal?.code ?? "REPLAY_UNSUPPORTED_INPUT";
    const reason = source.refusal?.detail || REFUSAL_FALLBACKS[code] || UNSUPPORTED_FALLBACK;
    const edit = EDITABLE_REFUSALS.has(code);
    return { ...disabled(reason), edit, href: edit ? "playground" : null };
  }
  if (source.fidelity === "messages_only") {
    return {
      label: NOT_EXACT_LABEL,
      disabled: false,
      reason: NOT_EXACT_REASON,
      edit: false,
      href: "playground",
    };
  }
  return { label: REPLAY_LABEL, disabled: false, reason: null, edit: false, href: "playground" };
}
