import { useState } from "react";

import { AuthBackLink } from "./auth-footer";
import { AuthLayout } from "./auth-layout";
import { ForgotPasswordForm } from "./forgot-password-form";
import { ForgotPasswordSent } from "./forgot-password-sent";

/**
 * Asks for the account email and mails a reset link. Anyone may open it, signed in or not, since
 * it is how a forgotten password is recovered.
 */
export function ForgotPasswordPage() {
  const [sentTo, setSentTo] = useState<string | null>(null);

  return (
    <AuthLayout
      title="Reset your"
      accent="password"
      description={
        sentTo === null
          ? "Enter your account email. If it matches an account, we'll send a link that works for 1 hour."
          : undefined
      }
    >
      {sentTo === null ? (
        <>
          <ForgotPasswordForm onSent={setSentTo} />
          <AuthBackLink />
        </>
      ) : (
        <ForgotPasswordSent email={sentTo} />
      )}
    </AuthLayout>
  );
}
