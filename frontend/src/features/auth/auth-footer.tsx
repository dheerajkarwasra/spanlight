import { Link } from "@tanstack/react-router";
import type { ReactNode } from "react";

import { cn } from "@/lib/utils";

import { AUTH_LINK } from "./auth-controls";

/** The quiet line under an auth form, e.g. "New here? Create an account". */
export function AuthFooter({ children }: { children: ReactNode }) {
  return (
    <p className="flex flex-wrap items-center gap-x-1.5 gap-y-1 text-[15px] leading-[1.4] text-mono-600">
      {children}
    </p>
  );
}

interface AuthBackLinkProps {
  /** The page the person was heading for, so signing in again still lands there. */
  next?: string | undefined;
}

/** "Back to sign in", the way out of the forgot, reset and code steps. */
export function AuthBackLink({ next }: AuthBackLinkProps) {
  return (
    <Link
      to="/login"
      search={{ next }}
      className={cn(AUTH_LINK, "flex min-h-11 items-center self-start lg:min-h-0")}
    >
      Back to sign in
    </Link>
  );
}
