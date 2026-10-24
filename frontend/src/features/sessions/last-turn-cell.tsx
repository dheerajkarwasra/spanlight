import { Link } from "@tanstack/react-router";

import { UnknownValue } from "@/components/unknown-value";
import { PreviewPending } from "@/features/traces/preview-cell";
import type { SessionListItem } from "@/lib/api";

interface LastTurnCellProps {
  session: SessionListItem;
  orgId: string;
  projectId: string;
  /** The rows are kept from an earlier request (maybe one without previews) until the new one lands. */
  pending: boolean;
}

/** The last turn's input in one line, linking to that trace; "—" with a reason when there is none. */
export function LastTurnCell({ session, orgId, projectId, pending }: LastTurnCellProps) {
  if (session.last_trace_id === null) {
    return <UnknownValue reason="The session's latest trace is no longer stored" />;
  }
  if (pending) {
    return <PreviewPending />;
  }
  if (!session.last_input_preview) {
    return <UnknownValue reason="No input captured on the last turn" />;
  }
  return (
    <Link
      to="/$orgId/$projectId/traces/$traceId"
      params={{ orgId, projectId, traceId: session.last_trace_id }}
      title={session.last_input_preview}
      className="truncate rounded-sm text-muted-foreground hover:text-foreground hover:underline"
    >
      {session.last_input_preview}
    </Link>
  );
}
