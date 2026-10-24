import { ChildPayloadView, type ChildFallback } from "./child-payload-view";
import { PayloadSection, type PayloadLabel } from "./payload-section";

interface PayloadViewProps {
  label: PayloadLabel;
  value: unknown;
  /** The span's `truncated` flag; the badge goes on whichever payload was cut. */
  spanTruncated: boolean;
  /**
   * Where to look when the span has no such payload: its LLM descendants. Left out, the empty
   * note does not speak for children.
   */
  fallback?: ChildFallback;
}

/** One span payload: chat transcripts render as bubbles, everything else as text or JSON. */
export function PayloadView({ label, value, spanTruncated, fallback }: PayloadViewProps) {
  const isEmpty = value === null || value === undefined;
  const [child, ...rest] = fallback?.candidates ?? [];
  if (isEmpty && fallback && child) {
    return (
      <ChildPayloadView label={label} child={child} fallback={{ ...fallback, candidates: rest }} />
    );
  }
  return (
    <PayloadSection
      label={label}
      value={value}
      spanTruncated={spanTruncated}
      childrenChecked={fallback !== undefined}
    />
  );
}
