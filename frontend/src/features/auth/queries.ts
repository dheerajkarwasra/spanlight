import { useQuery, useSuspenseQuery, type QueryClient } from "@tanstack/react-query";

import { authApi, isApiError, queryKeys, type Me } from "@/lib/api";

/** Resolves to the signed-in user, or null when there is no session. */
export async function fetchMeOrNull(): Promise<Me | null> {
  try {
    return await authApi.me();
  } catch (error) {
    if (isApiError(error) && error.status === 401) {
      return null;
    }
    throw error;
  }
}

export const meQueryOptions = {
  queryKey: queryKeys.me,
  queryFn: fetchMeOrNull,
  staleTime: 60_000,
};

/** Route guards: reuse the cached session if there is one, otherwise ask the server. */
export function ensureMe(queryClient: QueryClient): Promise<Me | null> {
  return queryClient.query({ ...meQueryOptions, staleTime: "static" });
}

/**
 * Re-reads the session from the server after it changed (sign in, sign up,
 * demo, joining or creating an org). Invalidating alone is not enough: route
 * guards read the cache, and there may be no mounted observer to trigger a
 * refetch before the next navigation.
 */
export function refreshMe(queryClient: QueryClient): Promise<Me | null> {
  return queryClient.query({ ...meQueryOptions, staleTime: 0 });
}

/** The signed-in user. Only use below the authenticated layout, where `me` is guaranteed. */
export function useMe(): Me {
  const { data } = useSuspenseQuery(meQueryOptions);
  if (!data) {
    throw new Error("useMe() called outside the authenticated layout");
  }
  return data;
}

/**
 * Whether every signed-in page is locked until `me` accepts the server's terms version. The server
 * reports no version to the demo account, which can't accept.
 */
export function termsPending(me: Me): boolean {
  return (
    me.terms_current_version !== null && me.terms_accepted_version !== me.terms_current_version
  );
}

/**
 * Whether signup shows the terms line: the server asks new accounts to accept a terms version.
 * The form sends `accept_terms` only when it does.
 */
export function useShowsTermsLine(): boolean {
  const terms = useQuery({
    queryKey: queryKeys.authTerms,
    queryFn: authApi.terms,
    // The version changes with the operator's settings, not with use (as `useOAuthProviders`).
    staleTime: 5 * 60_000,
    retry: false,
  });
  return Boolean(terms.data?.version);
}
