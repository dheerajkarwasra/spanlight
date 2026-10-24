import type { SortOrder } from "@/components/data-table";
import type { UserListQuery, UserSort } from "@/lib/api";

import type { UsersSearch } from "./search";

export const USER_PAGE_SIZE = 50;

/** The sortable columns in the order they appear, with their header and phone-menu labels. */
export const SORT_COLUMNS: readonly { sort: UserSort; label: string }[] = [
  { sort: "traces", label: "Traces" },
  { sort: "errors", label: "Errors" },
  { sort: "cost", label: "Cost" },
];

/** Footer wording for the active sort. Unknowns sort last in both directions. */
export function sortPhrase(sort: UserSort, order: SortOrder): string {
  const direction = order === "asc" ? "lowest first" : "highest first";
  return `sorted by ${sort}, ${direction}${sort === "cost" ? ", unpriced last" : ""}`;
}

/**
 * Search params after choosing a sort: the window, environment and search params are kept, and
 * the defaults (`cost`, `desc`) are left out of the URL.
 */
export function withSort<T extends UsersSearch>(search: T, sort: UserSort, order: SortOrder): T {
  return {
    ...search,
    sort: sort === "cost" ? undefined : sort,
    order: order === "desc" ? undefined : order,
  };
}

/** The list state the URL carries besides the window. */
export type UsersListState = Pick<UsersSearch, "sort" | "order" | "q">;

/** Maps the URL list state, the resolved time range and the environment onto the list's query. */
export function toUserListQuery(
  { sort, order, q }: UsersListState,
  window: { from: string; to: string },
  environment: string | undefined,
): Omit<UserListQuery, "cursor"> {
  return { from: window.from, to: window.to, sort, order, q, environment, limit: USER_PAGE_SIZE };
}

/** The window params every project page shares; kept when moving between the list and a user. */
export function keepWindow(previous: {
  range?: UsersSearch["range"];
  from?: string | undefined;
  to?: string | undefined;
  env?: string | undefined;
}): Pick<UsersSearch, "range" | "from" | "to" | "env"> {
  return { range: previous.range, from: previous.from, to: previous.to, env: previous.env };
}

/*
 * The list's sort, direction and search from the last visit, so "Back to users" on the detail page
 * returns to the same list. In memory only, like the traces list's remembered search.
 */
let lastList: UsersListState | null = null;

export function rememberUsersList(list: UsersListState): void {
  lastList = list;
}

export function recallUsersList(): Partial<UsersListState> {
  return lastList ?? {};
}
