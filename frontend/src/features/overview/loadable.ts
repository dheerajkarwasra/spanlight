import type { UseQueryResult } from "@tanstack/react-query";

/** A query's state as a card needs it: data, still loading, or failed with a retry. */
export type Loadable<T> =
  { status: "success"; data: T } | { status: "pending" } | { status: "error"; retry: () => void };

/** Turns a query into what a card renders: data, a loading state or a retry. */
export function loadable<T, R>(query: UseQueryResult<T>, select: (data: T) => R): Loadable<R> {
  if (query.isPending) {
    return { status: "pending" };
  }
  if (query.isError) {
    return {
      status: "error",
      retry: () => {
        void query.refetch();
      },
    };
  }
  return { status: "success", data: select(query.data) };
}
