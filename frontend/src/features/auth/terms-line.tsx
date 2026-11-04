import { Link } from "@tanstack/react-router";
import type { ReactNode } from "react";

import { LEGAL_PATHS } from "@/features/landing/links";
import { LEGAL_PAGES_ENABLED } from "@/features/legal/legal-enabled";

import { useShowsTermsLine } from "./queries";

type LegalPath = (typeof LEGAL_PATHS)[keyof typeof LEGAL_PATHS];

/**
 * A link to a legal page, opened in the same tab. A build without the legal pages has nowhere to
 * link to, so it renders the name alone.
 */
export function LegalLink({ to, children }: { to: LegalPath; children: ReactNode }) {
  if (!LEGAL_PAGES_ENABLED) {
    return children;
  }
  return (
    <Link to={to} className="rounded-xs font-semibold text-accent underline underline-offset-2">
      {children}
    </Link>
  );
}

/** "By creating an account you agree to the Terms…", under each way to sign up. */
export function TermsLine() {
  if (!useShowsTermsLine()) {
    return null;
  }
  return (
    <p className="text-center text-xs font-medium text-muted-foreground">
      By creating an account you agree to the <LegalLink to={LEGAL_PATHS.terms}>Terms</LegalLink>{" "}
      and acknowledge the <LegalLink to={LEGAL_PATHS.privacy}>Privacy policy</LegalLink> and{" "}
      <LegalLink to={LEGAL_PATHS.dpa}>DPA</LegalLink>.
    </p>
  );
}
