import { useMutation } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { CircleAlert, CircleCheck, Mail } from "lucide-react";
import { useEffect, useRef, useState, type ReactNode } from "react";

import { Callout } from "@/components/callout";
import { Button } from "@/components/ui/button";
import { authApi, errorMessage } from "@/lib/api";
import { readLastProject } from "@/lib/last-project";

import { AuthLayout } from "./auth-layout";
import { AuthStatusTile } from "./auth-status-tile";
import { clearFragment, readFragmentParam } from "./fragment-token";

// The shape the server mints (`app.alerts.unsubscribe`): a user id in hex, a dot, an HMAC.
const TOKEN_SHAPE = /^[0-9a-f]{32}\.[A-Za-z0-9_-]{43}$/;

/**
 * Where a digest's unsubscribe link lands: `/unsubscribe#token=…`. Opening it changes nothing (mail
 * scanners open links too); the button sends the token to the same route mail clients use for
 * one-click unsubscribe. No session is needed, and no email address is shown: whoever holds the
 * link may not own the account. The server answers every token alike, so only a link that is not
 * shaped like one is reported as not valid.
 */
export function UnsubscribePage() {
  const [token] = useState(() => {
    const value = readFragmentParam("token");
    return value !== null && TOKEN_SHAPE.test(value) ? value : null;
  });
  const heading = useRef<HTMLHeadingElement>(null);

  const unsubscribe = useMutation({
    // The token is a credential: it stays in this page's memory, not in the mutation cache.
    gcTime: 0,
    mutationFn: () => authApi.unsubscribe(token ?? ""),
  });

  useEffect(() => {
    clearFragment();
  }, []);

  // The route names the page; each state renames the tab, so it says what happened.
  const title =
    token === null ? "Link not valid" : unsubscribe.isSuccess ? "Unsubscribed" : "Unsubscribe";
  useEffect(() => {
    document.title = `${title} · Spanlight`;
  }, [title]);

  useEffect(() => {
    // The button is gone once it worked, so focus moves to the headline that says so.
    if (unsubscribe.isSuccess) {
      heading.current?.focus();
    }
  }, [unsubscribe.isSuccess]);

  if (token === null) {
    return (
      <AuthLayout
        title="Weekly"
        accent="digest"
        status={<AuthStatusTile icon={CircleAlert} tone="danger" />}
      >
        <Callout tone="danger" role="alert">
          This link is not valid. You can turn off the weekly digest in Settings › Account.
        </Callout>
        <Button asChild variant="primary" size="lg">
          {accountLink("Open Settings › Account")}
        </Button>
      </AuthLayout>
    );
  }
  if (unsubscribe.isSuccess) {
    return (
      <AuthLayout
        title="You're"
        accent="unsubscribed."
        headingRef={heading}
        description={
          <>
            You won&apos;t get the weekly digest anymore. Turn it back on in{" "}
            {accountLink(
              "Settings › Account",
              "rounded-xs whitespace-nowrap text-foreground underline underline-offset-2",
            )}
            .
          </>
        }
        status={<AuthStatusTile icon={CircleCheck} tone="success" />}
      >
        {null}
      </AuthLayout>
    );
  }
  return (
    <AuthLayout
      title="Weekly"
      accent="digest"
      description="You'll stop getting the Monday summary of each project's week, for every organization you belong to."
      status={<AuthStatusTile icon={Mail} tone="accent" />}
    >
      {unsubscribe.isError ? (
        <Callout tone="danger" role="alert">
          {errorMessage(unsubscribe.error)}
        </Callout>
      ) : null}
      <Button
        variant="primary"
        size="lg"
        loading={unsubscribe.isPending}
        onClick={() => {
          unsubscribe.mutate();
        }}
      >
        Unsubscribe from the weekly digest
      </Button>
    </AuthLayout>
  );
}

/** Settings › Account in the last project this browser opened; home (sign-in first) without one. */
function accountLink(children: ReactNode, className?: string) {
  const last = readLastProject();
  return last ? (
    <Link
      to="/$orgId/$projectId/settings/account"
      params={{ orgId: last.orgId, projectId: last.projectId }}
      className={className}
    >
      {children}
    </Link>
  ) : (
    <Link to="/" className={className}>
      {children}
    </Link>
  );
}
