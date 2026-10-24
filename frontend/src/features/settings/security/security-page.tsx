import { OAuthConnectionsSection } from "./oauth-connections-section";
import { OAuthLinkError } from "./oauth-link-error";
import { TotpSection } from "./totp-section";
import { TwoFactorRequiredNotice } from "./two-factor-required-notice";
import { SessionsSection } from "../sessions-section";

/**
 * Settings › Security: two-factor authentication, the ways to sign in, and the devices signed in.
 * It has no project of its own, so it renders both inside the project's settings and, for someone
 * locked out of every project, on its own at `/account/security`.
 */
export function SecurityPage() {
  return (
    <div className="flex max-w-[720px] flex-col gap-5">
      <TwoFactorRequiredNotice />
      <TotpSection />
      <OAuthLinkError />
      <OAuthConnectionsSection />
      <SessionsSection />
    </div>
  );
}
