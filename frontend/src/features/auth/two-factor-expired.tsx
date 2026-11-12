import { Link } from "@tanstack/react-router";

import { Button, Callout } from "./auth-controls";

import { AuthLayout } from "./auth-layout";
import { EXPIRED_MESSAGE } from "./two-factor-flow";

interface TwoFactorExpiredProps {
  next: string | undefined;
}

/** The challenge is gone (a reload, five minutes, a password change): only signing in again helps. */
export function TwoFactorExpired({ next }: TwoFactorExpiredProps) {
  return (
    <AuthLayout title="One more" accent="step.">
      <Callout tone="danger" role="alert">
        {EXPIRED_MESSAGE}
      </Callout>
      <Button asChild variant="primary" size="lg">
        <Link to="/login" search={{ next }}>
          Back to sign in
        </Link>
      </Button>
    </AuthLayout>
  );
}
