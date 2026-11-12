import { getRouteApi } from "@tanstack/react-router";
import { useId, useRef } from "react";

import { AuthBackLink } from "./auth-footer";
import { AuthLayout } from "./auth-layout";
import { TwoFactorExpired } from "./two-factor-expired";
import { TwoFactorForm } from "./two-factor-form";
import { useTwoFactorFlow } from "./use-two-factor-flow";

const routeApi = getRouteApi("/login/two-factor");

const INSTRUCTIONS = {
  code: "Enter the 6-digit code from your authenticator app.",
  recovery: "Enter one of your recovery codes. Each code works once.",
} as const;

/**
 * The second step of signing in, for an account with two-factor authentication: reached after a
 * correct password (the challenge is in memory) or from GitHub and Google (it is in the URL
 * fragment). No one is signed in until the code is accepted.
 */
export function TwoFactorPage() {
  const { next } = routeApi.useSearch();
  const inputRef = useRef<HTMLInputElement>(null);
  const flow = useTwoFactorFlow(next, inputRef);
  const instructionId = useId();

  if (flow.expired) {
    return <TwoFactorExpired next={next} />;
  }

  return (
    <AuthLayout
      title="One more"
      accent="step."
      description={<span id={instructionId}>{INSTRUCTIONS[flow.mode]}</span>}
    >
      <TwoFactorForm flow={flow} instructionId={instructionId} inputRef={inputRef} />
      <AuthBackLink next={next} />
    </AuthLayout>
  );
}
