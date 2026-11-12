import { Link } from "@tanstack/react-router";

import { Button, Callout } from "./auth-controls";

interface ForgotPasswordSentProps {
  email: string;
}

/**
 * What replaces the form once the request was accepted. It says the same thing whether or not the
 * address has an account, because the server does.
 */
export function ForgotPasswordSent({ email }: ForgotPasswordSentProps) {
  return (
    <div className="flex flex-col gap-6">
      <Callout tone="success" role="status">
        If <span className="font-semibold">{email}</span> has a Spanlight account, a reset link is
        on its way.
      </Callout>
      <div className="flex flex-col gap-6">
        <Button asChild size="lg">
          <Link to="/login" search={{}}>
            Back to sign in
          </Link>
        </Button>
        <p className="text-sm leading-normal text-mono-600">
          Didn&apos;t get it? Check your spam folder, then try again in a few minutes.
        </p>
      </div>
    </div>
  );
}
