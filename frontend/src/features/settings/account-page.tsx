import { Link } from "@tanstack/react-router";

import { useMe } from "@/features/auth";
import { LEGAL_PATHS } from "@/features/landing/links";
import { LEGAL_PAGES_ENABLED } from "@/features/legal/legal-enabled";

import { AccountDangerZone } from "./account-danger-zone";
import { EmailSection } from "./email-section";
import { ProfileSection } from "./profile-section";
import { isDemoAccount } from "./security/sign-in-methods";
import { SignOutSection } from "./sign-out-section";

const LEGAL_LINK_CLASS = "rounded-xs underline underline-offset-2 hover:text-foreground";

/**
 * Settings › Account: who you are, the emails you get, signing out and deleting the account. Active
 * sessions live on Security. The shared demo account can't be changed and gets no email, so it
 * gets no edits, no Email card and no danger zone.
 */
export function AccountPage() {
  const demo = isDemoAccount(useMe().memberships);
  return (
    <div className="flex max-w-[720px] flex-col gap-5">
      <ProfileSection editable={!demo} />
      {demo ? null : <EmailSection />}
      <SignOutSection />
      {demo ? null : <AccountDangerZone />}
      {LEGAL_PAGES_ENABLED && <LegalLinks />}
    </div>
  );
}

function LegalLinks() {
  return (
    <nav
      aria-label="Legal"
      className="flex min-h-6 flex-wrap items-center gap-x-2 text-xs font-medium text-muted-foreground"
    >
      <span>Legal:</span>
      <Link to={LEGAL_PATHS.privacy} className={LEGAL_LINK_CLASS}>
        Privacy policy
      </Link>
      <span aria-hidden>·</span>
      <Link to={LEGAL_PATHS.terms} className={LEGAL_LINK_CLASS}>
        Terms
      </Link>
      <span aria-hidden>·</span>
      <Link to={LEGAL_PATHS.dpa} className={LEGAL_LINK_CLASS}>
        DPA
      </Link>
    </nav>
  );
}
