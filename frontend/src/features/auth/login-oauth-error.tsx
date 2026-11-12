import { useNavigate } from "@tanstack/react-router";
import { useEffect, useRef, useState } from "react";

import { Callout } from "./auth-controls";
import { signInErrorMessage } from "./login-flow";
import { recallOAuthProvider } from "./oauth-provider-hint";

interface LoginOAuthErrorProps {
  /** The `?error=` code the OAuth callback redirected back with, if any. */
  code: string | undefined;
}

/**
 * The banner above the sign-in form after a failed GitHub or Google sign-in. It is announced and
 * takes focus once, then the code leaves the address bar, so a reload does not show it again.
 */
export function LoginOAuthError({ code }: LoginOAuthErrorProps) {
  const navigate = useNavigate();
  const [message] = useState(() => signInErrorMessage(code, recallOAuthProvider()));
  const bannerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (message === null) {
      return;
    }
    bannerRef.current?.focus();
    void navigate({
      to: "/login",
      search: (previous) => ({ ...previous, error: undefined }),
      replace: true,
    });
  }, [message, navigate]);

  if (message === null) {
    return null;
  }
  return (
    <div ref={bannerRef} role="alert" tabIndex={-1} className="rounded-input outline-none">
      <Callout tone="danger">{message}</Callout>
    </div>
  );
}
