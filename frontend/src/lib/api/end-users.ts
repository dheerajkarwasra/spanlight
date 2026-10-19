import { api } from "./client";
import { projectPath } from "./paths";
import type { UserDetail, UserDetailQuery, UserList, UserListQuery } from "./types";

/*
 * Browsers resolve "." and ".." as path segments even when percent-encoded, so a user or session
 * id that looks like one travels with a "~" prefix: user ids in the app route and on the wire,
 * session ids in the app route only. Ids that already start with "~" get one more, so the mapping
 * is reversible; the users API strips exactly one leading "~".
 */

/** The id as a path segment: `.`, `..` and ids starting with `~` get a `~` prefix. */
export function toPathId(userId: string): string {
  return userId === "." || userId === ".." || userId.startsWith("~") ? `~${userId}` : userId;
}

/** The real id from a path segment built by `toPathId`. */
export function fromPathId(pathId: string): string {
  return pathId.startsWith("~") ? pathId.slice(1) : pathId;
}

function usersPath(projectId: string): string {
  return `${projectPath(projectId)}/users`;
}

/**
 * End-user analytics (the `user_id` set on traces). Reads need `project:read`. Figures cover the
 * window widened to whole UTC days.
 */
export const endUsersApi = {
  /** Sorted by `sort` (default `cost`), keyset-paginated. */
  list: (projectId: string, query: UserListQuery = {}): Promise<UserList> =>
    api.get<UserList>(usersPath(projectId), { ...query }),
  /** The user's totals, one row per UTC day and the recent sessions. */
  get: (projectId: string, userId: string, query: UserDetailQuery = {}): Promise<UserDetail> =>
    api.get<UserDetail>(`${usersPath(projectId)}/${encodeURIComponent(toPathId(userId))}`, {
      ...query,
    }),
};
