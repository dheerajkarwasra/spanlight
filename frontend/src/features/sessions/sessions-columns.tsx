import { Link, type LinkProps } from "@tanstack/react-router";

import type { DataColumn } from "@/components/data-table";
import { UnknownValue, ValueOrUnknown } from "@/components/unknown-value";
import { useProjectParams } from "@/features/shell/project-context";
import { toPathId, type SessionListItem } from "@/lib/api";
import { formatDuration, formatInteger, pluralize } from "@/lib/format";
import type { ProjectSearch } from "@/lib/time-range";
import { cn } from "@/lib/utils";

import { LastTurnCell } from "./last-turn-cell";
import { formatSessionWindow } from "./session-format";
import { SessionCost } from "./session-tile";

type Column = DataColumn<SessionListItem>;

interface SessionColumnsContext {
  orgId: string;
  projectId: string;
  /** The first page's 99th percentile of session cost; later pages don't repeat it. */
  costP99: string | null;
  /** The rows are kept from an earlier request until the new one lands: previews show a placeholder. */
  previewsPending: boolean;
}

/** The window params every project page shares; kept when opening a session or its last turn. */
function keepWindow(previous: ProjectSearch): ProjectSearch {
  return { range: previous.range, from: previous.from, to: previous.to, env: previous.env };
}

/**
 * Table widths (px) below which a column starts hidden, so the table never scrolls sideways: the
 * sum of the visible tracks' minimums plus the 40 px Table settings track. The last-turn preview
 * goes first, then the user, then the duration; all stay in Table settings.
 */
const SHOW_LAST_TURN = 1024;
const SHOW_USER = 844;
const SHOW_DURATION = 724;

/** Spec §8 S2: Session (sticky), User, Turns, Duration, Tokens, Cost, Errors, Last turn. */
export function sessionColumns({
  orgId,
  projectId,
  costP99,
  previewsPending,
}: SessionColumnsContext): Column[] {
  return [
    {
      id: "session",
      header: "Session",
      width: "minmax(220px,1.2fr)",
      sticky: true,
      cell: (session) => (
        <span className="flex min-w-0 flex-col">
          <Link
            to="/$orgId/$projectId/sessions/$sessionId"
            params={{ orgId, projectId, sessionId: toPathId(session.session_id) }}
            search={keepWindow}
            title={session.session_id}
            className="truncate rounded-sm font-mono text-code leading-4 text-foreground hover:underline"
          >
            {session.session_id}
          </Link>
          <span className="truncate text-xs leading-4 text-muted-foreground">
            {formatSessionWindow(session.first_at, session.last_at)}
          </span>
        </span>
      ),
    },
    {
      id: "user",
      header: "User",
      width: "minmax(120px,0.8fr)",
      hideable: true,
      minContainer: SHOW_USER,
      cell: (session) =>
        session.external_user_id !== null ? (
          <span title={session.external_user_id} className="truncate font-mono text-label">
            {session.external_user_id}
          </span>
        ) : session.user_count > 1 ? (
          <span className="text-muted-foreground tabular">
            {pluralize(session.user_count, "user")}
          </span>
        ) : (
          <UnknownValue reason="No user ID on these traces" />
        ),
    },
    {
      id: "turns",
      header: "Turns",
      width: "80px",
      align: "end",
      sortKey: "turns",
      cell: (session) => formatInteger(session.trace_count),
    },
    {
      id: "duration",
      header: "Duration",
      width: "96px",
      align: "end",
      hideable: true,
      minContainer: SHOW_DURATION,
      cell: (session) => (
        <span className="text-muted-foreground">
          <ValueOrUnknown value={formatDuration(session.duration_ms)} reason="No timing data" />
        </span>
      ),
    },
    {
      id: "tokens",
      header: "Tokens",
      width: "96px",
      align: "end",
      sortKey: "tokens",
      cell: (session) =>
        session.input_tokens === null && session.output_tokens === null ? (
          <UnknownValue reason="No call in this session reported token usage" />
        ) : (
          <span
            title={`${formatInteger(session.input_tokens) ?? "—"} input · ${formatInteger(session.output_tokens) ?? "—"} output`}
            className="text-muted-foreground"
          >
            {formatInteger((session.input_tokens ?? 0) + (session.output_tokens ?? 0))}
          </span>
        ),
    },
    {
      id: "cost",
      header: "Cost",
      width: "112px",
      align: "end",
      sortKey: "cost",
      cell: (session) => <SessionCost session={session} costP99={costP99} />,
    },
    {
      id: "errors",
      header: "Errors",
      width: "80px",
      align: "end",
      sortKey: "errors",
      cell: (session) => (
        <span
          className={cn(session.error_count > 0 ? "text-danger-text" : "text-muted-foreground")}
        >
          {formatInteger(session.error_count)}
        </span>
      ),
    },
    {
      id: "last_turn",
      header: "Last turn",
      width: "minmax(180px,1.4fr)",
      hideable: true,
      minContainer: SHOW_LAST_TURN,
      sortKey: "last",
      cell: (session) => (
        <LastTurnCell
          session={session}
          orgId={orgId}
          projectId={projectId}
          pending={previewsPending}
        />
      ),
    },
  ];
}

/** Where a plain click on a session's row goes: the session's page, keeping the window. */
export function useSessionHref(): (session: SessionListItem) => LinkProps {
  const { orgId, projectId } = useProjectParams();
  return (session) => ({
    to: "/$orgId/$projectId/sessions/$sessionId",
    params: { orgId, projectId, sessionId: toPathId(session.session_id) },
    search: keepWindow,
  });
}
