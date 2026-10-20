import { MutationCache, QueryCache, QueryClient, type Query } from "@tanstack/react-query";

import { isApiError, isTwoFactorRequired, queryKeys } from "@/lib/api";

/** Don't retry errors that won't fix themselves (4xx); retry transient ones twice. */
function shouldRetry(failureCount: number, error: unknown): boolean {
  if (isApiError(error) && error.status >= 400 && error.status < 500 && error.status !== 429) {
    return false;
  }
  return failureCount < 2;
}

/** `["project", id, "detail"]`: the query the app shell reads to decide whether a project opens. */
function isProjectDetail(query: Query): boolean {
  return query.queryKey[0] === "project" && query.queryKey[2] === "detail";
}

/** `["project", id, "datasets", ...]`: every dataset list, detail, item and picker query. */
function isDatasetQuery(query: Query): boolean {
  return query.queryKey[0] === "project" && query.queryKey[2] === "datasets";
}

/** `me.totp_enabled`, or null when the cached value is not a signed-in user (signed out). */
function totpEnabledOf(me: unknown): boolean | null {
  if (me !== null && typeof me === "object" && "totp_enabled" in me) {
    return typeof me.totp_enabled === "boolean" ? me.totp_enabled : null;
  }
  return null;
}

function isTwoFactorBlocked(query: Query): boolean {
  return query.state.status === "error" && isTwoFactorRequired(query.state.error);
}

export function createQueryClient(): QueryClient {
  const client: QueryClient = new QueryClient({
    queryCache: new QueryCache({
      onError: (error) => {
        // The organization started requiring two-factor authentication after this page loaded. The
        // shell shows its "turn it on" card from the project query, so make that one look again;
        // one that already failed this way is left alone, which keeps this from looping.
        if (isTwoFactorRequired(error)) {
          void client.invalidateQueries({
            predicate: (query) => isProjectDetail(query) && !isTwoFactorBlocked(query),
          });
        }
      },
    }),
    mutationCache: new MutationCache({
      onError: (error) => {
        // Someone locked the dataset after this page loaded. Reload the dataset queries so the
        // detail page hides its write controls and pickers stop offering it; the mutation's own
        // error message is shown as before.
        if (isApiError(error) && error.code === "DATASET_LOCKED") {
          void client.invalidateQueries({ predicate: isDatasetQuery });
        }
      },
    }),
    defaultOptions: {
      queries: {
        staleTime: 30_000,
        retry: shouldRetry,
        refetchOnWindowFocus: true,
      },
      mutations: {
        retry: false,
        // A mutation's variables and answer can be a password, a code, a token or a new secret
        // (sign-in, reset, verify, a created key). Keep them only while a screen is showing the
        // result, not for the default five minutes after it is gone.
        gcTime: 0,
      },
    },
  });

  // Once the signed-in person turns two-factor authentication on, whatever was locked out by the
  // organization's requirement can load again: look at every query that failed with it, not only the
  // project the shell watches.
  let totpEnabled: boolean | null = null;
  client.getQueryCache().subscribe((event) => {
    if (event.type !== "updated" || event.action.type !== "success") {
      return;
    }
    const query = event.query as Query;
    if (query.queryKey.length !== 1 || query.queryKey[0] !== queryKeys.me[0]) {
      return;
    }
    const now = totpEnabledOf(query.state.data);
    if (totpEnabled === false && now === true) {
      void client.invalidateQueries({ predicate: isTwoFactorBlocked });
    }
    totpEnabled = now;
  });

  return client;
}
