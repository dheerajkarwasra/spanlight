import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { useEffect, useRef, useState } from "react";

import { authApi, errorMessage, isApiError } from "@/lib/api";
import { readLastProject } from "@/lib/last-project";

import { Button, Callout } from "./auth-controls";
import { AuthLayout } from "./auth-layout";
import { clearFragment, readFragmentParam } from "./fragment-token";
import { isDeadLinkError } from "./verify-email-flow";

/**
 * Where the email-change link lands: `/confirm-email-change#token=…`. Like a reset link it needs no
 * session; the token is read from the fragment once, sent to the confirm route and taken out of the
 * address bar. A confirmed change ends every session, this browser's included.
 */
export function ConfirmEmailChangePage() {
  const queryClient = useQueryClient();
  const [token] = useState(() => readFragmentParam("token"));
  const started = useRef(false);

  const confirm = useMutation({
    // The token is a credential: it stays in this page's memory, not in the mutation cache.
    gcTime: 0,
    mutationFn: () => {
      if (token === null) {
        throw new Error("No token to confirm");
      }
      return authApi.confirmEmailChange(token);
    },
    // Every session ended, so nothing cached for the old one may be shown again.
    onSuccess: () => {
      queryClient.clear();
    },
  });
  const { mutate } = confirm;

  useEffect(() => {
    clearFragment();
    // A token is single use: never send it twice, as development's double effect would.
    if (token === null || started.current) {
      return;
    }
    started.current = true;
    mutate();
  }, [token, mutate]);

  if (confirm.isSuccess) {
    return (
      <AuthLayout
        title="Email"
        accent="changed."
        description="Sign in again with your new address. For your security, every session was signed out."
      >
        <Button asChild variant="primary" size="lg">
          <Link to="/login" search={{}}>
            Sign in
          </Link>
        </Button>
      </AuthLayout>
    );
  }
  if (token !== null && !confirm.isError) {
    return (
      <AuthLayout
        title="Changing your"
        accent="email"
        description={<span role="status">Checking your link…</span>}
      >
        {null}
      </AuthLayout>
    );
  }

  // No token, or a dead one; anything else (offline, a server error) can be tried again.
  const retryable = confirm.isError && !isDeadLinkError(confirm.error);
  const taken = isApiError(confirm.error) && confirm.error.code === "EMAIL_TAKEN";
  // Settings › Account in the last project this browser opened; home when it opened none.
  const last = readLastProject();
  return (
    <AuthLayout title="Change your" accent="email">
      <Callout tone="danger" role="alert">
        {retryable || taken
          ? errorMessage(confirm.error)
          : "This link is invalid or has expired. Request a new one in Settings › Account."}
      </Callout>
      <div className="flex flex-col gap-6">
        {retryable ? (
          <Button
            variant="primary"
            size="lg"
            onClick={() => {
              mutate();
            }}
          >
            Try again
          </Button>
        ) : null}
        <Button asChild variant={retryable ? "secondary" : "primary"} size="lg">
          {last ? (
            <Link
              to="/$orgId/$projectId/settings/account"
              params={{ orgId: last.orgId, projectId: last.projectId }}
            >
              Open Settings › Account
            </Link>
          ) : (
            <Link to="/">Open Settings › Account</Link>
          )}
        </Button>
      </div>
    </AuthLayout>
  );
}
