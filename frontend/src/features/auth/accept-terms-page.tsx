import { useMutation, useQueryClient } from "@tanstack/react-query";
import { getRouteApi, useNavigate } from "@tanstack/react-router";
import { useState } from "react";

import { LEGAL_PATHS } from "@/features/landing/links";
import { useSignOut } from "@/features/shell/use-sign-out";
import { authApi, errorMessage, isApiError } from "@/lib/api";

import { Button, Callout } from "./auth-controls";
import { AuthLayout } from "./auth-layout";
import { refreshMe, termsPending, useMe } from "./queries";
import { safeNextPath } from "./search";
import { LegalLink } from "./terms-line";

const routeApi = getRouteApi("/accept-terms");

/**
 * The terms gate (Figma "Accept terms"): every signed-in page redirects here until the current
 * version is accepted. Accepting re-reads `me` before going on to `next`, so the gate there reads
 * the accepted version and lets them through.
 */
export function AcceptTermsPage() {
  const { next } = routeApi.useSearch();
  const me = useMe();
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const signOut = useSignOut();
  const [error, setError] = useState<string | null>(null);

  const goOn = () => navigate({ href: safeNextPath(next) ?? "/", replace: true });

  const accept = useMutation({
    mutationFn: authApi.acceptTerms,
    onSuccess: async () => {
      await refreshMe(queryClient);
      await goOn();
    },
    onError: async (failure) => {
      setError(errorMessage(failure));
      // The server moved to another version, or dropped its terms: show what it asks for now.
      if (
        isApiError(failure) &&
        (failure.code === "TERMS_VERSION_MISMATCH" || failure.code === "TERMS_NOT_CONFIGURED")
      ) {
        const fresh = await refreshMe(queryClient);
        if (fresh && !termsPending(fresh)) {
          await goOn();
        }
      }
    },
  });

  const version = me.terms_current_version;
  // Accepted (or no longer asked for): the mutation is already navigating on to `next`.
  if (!termsPending(me) || version === null) {
    return null;
  }
  const first = me.terms_accepted_version === null;
  const terms = <LegalLink to={LEGAL_PATHS.terms}>Terms</LegalLink>;

  return (
    <AuthLayout
      title={first ? "Accept the" : "Review the updated"}
      accent="terms"
      description={
        <>
          {first ? (
            <>To use Spanlight, accept the {terms}.</>
          ) : (
            <>We updated the {terms}. Accept them to keep using Spanlight.</>
          )}{" "}
          The <LegalLink to={LEGAL_PATHS.privacy}>Privacy policy</LegalLink> and{" "}
          <LegalLink to={LEGAL_PATHS.dpa}>DPA</LegalLink> explain how we handle your data.
        </>
      }
    >
      {error ? (
        <Callout tone="danger" role="alert">
          {error}
        </Callout>
      ) : null}
      <div className="flex flex-col gap-3">
        <Button
          variant="primary"
          size="lg"
          loading={accept.isPending}
          onClick={() => {
            setError(null);
            accept.mutate(version);
          }}
        >
          Accept and continue
        </Button>
        <Button
          variant="ghost"
          size="lg"
          loading={signOut.isPending}
          onClick={() => {
            signOut.mutate();
          }}
        >
          Sign out
        </Button>
      </div>
      <p className="font-mono text-xs leading-normal [overflow-wrap:anywhere] text-mono-600">
        Terms version {version} · Signed in as {me.user.email}
      </p>
    </AuthLayout>
  );
}
