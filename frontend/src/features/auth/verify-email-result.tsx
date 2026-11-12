import { Link } from "@tanstack/react-router";

import { AUTH_NOTE, Button, Callout } from "./auth-controls";

import { AuthLayout } from "./auth-layout";
import { ResendNote } from "./resend-note";
import { useResendVerification } from "./use-resend-verification";

interface VerifyEmailResultProps {
  /** Whether someone is signed in here: only they can ask for a new link. */
  signedIn: boolean;
}

function ContinueButton({ signedIn }: VerifyEmailResultProps) {
  return (
    <Button asChild variant="primary" size="lg">
      {signedIn ? (
        <Link to="/">Continue to Spanlight</Link>
      ) : (
        <Link to="/login" search={{}}>
          Continue to Spanlight
        </Link>
      )}
    </Button>
  );
}

export function VerifyEmailChecking() {
  return (
    <AuthLayout
      title="Verifying your"
      accent="email"
      description={<span role="status">Checking your link…</span>}
    >
      {null}
    </AuthLayout>
  );
}

export function VerifyEmailSuccess({ signedIn }: VerifyEmailResultProps) {
  return (
    <AuthLayout
      title="Email"
      accent="verified."
      description="You can now turn on two-factor authentication in Settings › Security."
    >
      <ContinueButton signedIn={signedIn} />
    </AuthLayout>
  );
}

interface VerifyEmailRetryProps extends VerifyEmailResultProps {
  message: string;
  onRetry: () => void;
}

/** The check itself failed (offline, a server error). The link may still be good, so try again. */
export function VerifyEmailRetry({ message, signedIn, onRetry }: VerifyEmailRetryProps) {
  return (
    <AuthLayout title="Verify your" accent="email">
      <Callout tone="danger" role="alert">
        {message}
      </Callout>
      <div className="flex flex-col gap-6">
        <Button variant="primary" size="lg" onClick={onRetry}>
          Try again
        </Button>
        <Button asChild size="lg">
          {signedIn ? (
            <Link to="/">Continue to Spanlight</Link>
          ) : (
            <Link to="/login" search={{}}>
              Continue to Spanlight
            </Link>
          )}
        </Button>
      </div>
    </AuthLayout>
  );
}

export function VerifyEmailFailure({ signedIn }: VerifyEmailResultProps) {
  const resend = useResendVerification();
  return (
    <AuthLayout title="Verify your" accent="email">
      <Callout tone="danger" role="alert">
        This verification link is invalid or has expired.
      </Callout>
      <div className="flex flex-col gap-6">
        <ContinueButton signedIn={signedIn} />
        {signedIn ? (
          <>
            <Button size="lg" loading={resend.pending} onClick={resend.resend}>
              Resend link
            </Button>
            <ResendNote state={resend.state} />
          </>
        ) : (
          <p className={AUTH_NOTE}>
            Need a new link? Use Resend link in the banner at the top of the app.
          </p>
        )}
      </div>
    </AuthLayout>
  );
}
