import type { LinkProps } from "@tanstack/react-router";
import { UserRound } from "lucide-react";

import type { DataColumn } from "@/components/data-table";
import { useProjectParams } from "@/features/shell/project-context";
import { toPathId, type UserStats } from "@/lib/api";
import { formatInteger } from "@/lib/format";

import { lastSeenLabel } from "./user-format";
import { ErrorCount, UserCost, UserLink } from "./user-row";
import { keepWindow } from "./user-sort";

/** Spec §9: User (sticky), Traces, LLM calls, Errors, Cost, Tokens, Last seen. */
export const USER_COLUMNS: DataColumn<UserStats>[] = [
  {
    id: "user",
    header: "User",
    width: "minmax(220px,1fr)",
    sticky: true,
    cell: (user) => (
      <span className="flex min-w-0 items-center gap-2.5">
        <span
          aria-hidden
          className="flex size-6 shrink-0 items-center justify-center rounded-full bg-accent-subtle text-accent"
        >
          <UserRound className="size-3.5" />
        </span>
        <UserLink userId={user.external_user_id} />
      </span>
    ),
  },
  {
    id: "traces",
    header: "Traces",
    width: "88px",
    align: "end",
    sortKey: "traces",
    cell: (user) => formatInteger(user.traces),
  },
  {
    id: "llm_calls",
    header: "LLM calls",
    width: "96px",
    align: "end",
    hideable: true,
    minContainer: 760,
    cell: (user) => formatInteger(user.llm_calls),
  },
  {
    id: "errors",
    header: "Errors",
    width: "88px",
    align: "end",
    sortKey: "errors",
    cell: (user) => <ErrorCount count={user.errors} />,
  },
  {
    id: "cost",
    header: "Cost",
    width: "104px",
    align: "end",
    sortKey: "cost",
    cell: (user) => <UserCost user={user} />,
  },
  {
    id: "tokens",
    header: "Tokens",
    width: "104px",
    align: "end",
    hideable: true,
    minContainer: 860,
    cell: (user) => <span className="text-muted-foreground">{formatInteger(user.tokens)}</span>,
  },
  {
    id: "last_seen",
    header: "Last seen",
    width: "104px",
    hideable: true,
    cell: (user) => (
      <span className="truncate text-muted-foreground">{lastSeenLabel(user.last_seen_day)}</span>
    ),
  },
];

/** Where a plain click on a user's row goes: the user's page, in the same window. */
export function useUserHref(): (user: UserStats) => LinkProps {
  const { orgId, projectId } = useProjectParams();
  return (user) => ({
    to: "/$orgId/$projectId/users/$userId",
    params: { orgId, projectId, userId: toPathId(user.external_user_id) },
    search: keepWindow,
  });
}
