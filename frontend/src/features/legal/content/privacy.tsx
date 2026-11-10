import { Link } from "@tanstack/react-router";

import { LEGAL_PATHS } from "@/features/landing/links";

import { LEGAL_FACTS } from "../legal-facts";
import { LegalPage } from "../legal-page";
import { BackupsUnconfirmed, Fact, LegalList, LegalSection, LegalTable } from "../legal-parts";

export const VERSION = "2026-11-01";

const SECTIONS = {
  who: "Who runs Spanlight",
  what: "What we process, why and for how long",
  "customer-content": "Traces and other customer content",
  recipients: "Recipients",
  cookies: "Cookies and local storage",
  "not-done": "What we don't do",
  llm: "LLM features",
  rights: "Your rights",
  changes: "Changes to this policy",
} as const;

const CONTENTS = Object.entries(SECTIONS).map(([id, title]) => ({ id, title }));

const privacyEmail = <a href={`mailto:${LEGAL_FACTS.privacyEmail}`}>{LEGAL_FACTS.privacyEmail}</a>;

const CONTRACT = "Contract, Art. 6(1)(b) GDPR";
const LEGITIMATE_INTEREST = "Legitimate interest, Art. 6(1)(f) GDPR";

/** GDPR Art. 13 notice for the operator of spanlight.dev as controller. */
export function PrivacyPage() {
  return (
    <LegalPage title="Privacy policy" version={VERSION} contents={CONTENTS}>
      <p>
        This policy explains how we process personal data when you visit spanlight.dev or use the
        hosted Spanlight service. The service is for business use only.
      </p>

      <LegalSection id="who" title={SECTIONS.who}>
        <p>The controller under the GDPR is:</p>
        <p>
          <span className="text-foreground">{LEGAL_FACTS.operatorName}</span>
          <br />
          <span className="text-foreground">Postal address:</span>{" "}
          <Fact value={LEGAL_FACTS.postalAddress} what="postal address" />
          <br />
          {privacyEmail}
        </p>
        <p>
          We have not appointed a data protection officer, because we are below the threshold in §38
          BDSG.
        </p>
      </LegalSection>

      <LegalSection id="what" title={SECTIONS.what}>
        <LegalTable
          label="What we process"
          head={["Data", "Purpose", "Legal basis", "Kept"]}
          rows={[
            [
              "Account data: email, name, password hash, Google or GitHub sign-in links, two-factor state, memberships",
              "To run your account and the service",
              CONTRACT,
              "Until you delete your account in Settings → Account",
            ],
            [
              "Sessions: IP address, user agent",
              "To keep you signed in and protect your account",
              `${LEGITIMATE_INTEREST} (security)`,
              "Until the session expires: 7 days after last use, at most 30 days",
            ],
            [
              "Login attempts: email, IP address",
              "To stop password guessing",
              `${LEGITIMATE_INTEREST} (security)`,
              "1 day",
            ],
            [
              "Rate-limit records",
              "To protect the service from overload and abuse",
              `${LEGITIMATE_INTEREST} (security)`,
              "1 hour",
            ],
            [
              "Audit log: the acting member's email, IP address, user agent",
              "Accountability and security for your organisation",
              LEGITIMATE_INTEREST,
              "365 days. The log is append-only, so a former member's email stays in it until it ages out.",
            ],
            [
              "Invites: the invitee's email",
              "To invite someone to an organisation",
              LEGITIMATE_INTEREST,
              "Deleted 30 days after the invite is accepted or expires",
            ],
            [
              "Service emails: verification, password reset, invites, alerts",
              "To run the service",
              CONTRACT,
              "Verification and reset tokens are deleted within 1 day of use or expiry. The queued email is deleted 7 days after it was queued, once sent or failed.",
            ],
            [
              "Weekly digest, sent to your account email",
              "To summarise project activity for members",
              <>
                {LEGITIMATE_INTEREST}. A project admin can turn it off for a project, and you can
                turn it off for yourself in Settings › Account or with the unsubscribe link in each
                digest.
              </>,
              "The queued message is deleted 7 days after it was queued, once sent",
            ],
            [
              "Demo visits: session cookie, request logs (see below)",
              "To let visitors try the read-only demo",
              LEGITIMATE_INTEREST,
              "The demo session ends after 1 hour; request logs as below",
            ],
            [
              "Request logs: our API logs (method, path, status and duration; no IP address or query string) and Railway's edge logs",
              "To operate and secure the service",
              LEGITIMATE_INTEREST,
              <Fact value={LEGAL_FACTS.railwayLogRetention} what="Railway log retention" />,
            ],
            [
              "Backups: copies of the database, which holds the data above",
              "To restore the service after a failure",
              LEGITIMATE_INTEREST,
              <>
                The newest backup, 14 daily and 8 weekly: up to about 8 weeks
                <BackupsUnconfirmed />
              </>,
            ],
          ]}
        />
        <p>When you accept the terms we record the version and the time ({CONTRACT}).</p>
      </LegalSection>

      <LegalSection id="customer-content" title={SECTIONS["customer-content"]}>
        <p>
          Traces and spans hold your prompts, completions and metadata, and may contain personal
          data of your own end users. For this content you are the controller and we are your
          processor: we process it only on your instructions, as the{" "}
          <Link to={LEGAL_PATHS.dpa}>Data processing agreement</Link> sets out. Traces are kept for
          30 days by default; you can set 1 to 90 days per project.
        </p>
        <p>If you are an end user of one of our customers, contact that customer first.</p>
      </LegalSection>

      <LegalSection id="recipients" title={SECTIONS.recipients}>
        <p>
          The providers that process data for us are listed on the{" "}
          <Link to={LEGAL_PATHS.subprocessors}>Subprocessors</Link> page. Hosting is in the EU:{" "}
          {LEGAL_FACTS.hosting}.
        </p>
        <p>
          Railway is a US company. Any access to data from the US rests on Railway&apos;s data
          processing agreement with standard contractual clauses, or on the EU-US Data Privacy
          Framework.
        </p>
        <p>
          If you choose to sign in with Google or GitHub, that provider confirms your identity to us
          under its own terms.
        </p>
      </LegalSection>

      <LegalSection id="cookies" title={SECTIONS.cookies}>
        <p>
          We store only what is strictly necessary for the service you asked for, or what keeps a
          choice you made (TDDDG §25(2) no. 2). We therefore do not ask for consent.
        </p>
        <LegalTable
          label="Cookies and local storage"
          head={["Name", "Type", "Purpose", "Lifetime"]}
          rows={(
            [
              [["__Host-spl_session"], "Cookie", "Keeps you signed in", "Up to 30 days"],
              [
                ["__Host-spl_csrf"],
                "Cookie",
                "Protects your requests against forgery",
                "Up to 30 days",
              ],
              [
                ["spanlight-theme"],
                "localStorage",
                "Your light or dark theme",
                "Until you clear it",
              ],
              [
                ["spanlight-last-project"],
                "localStorage",
                "Opens the project you used last",
                "Until you clear it",
              ],
              [
                ["spanlight.rail", "spanlight.trace-info"],
                "localStorage",
                "Whether the sidebar is collapsed and the trace info panel is open",
                "Until you clear them",
              ],
              [
                [
                  "spanlight-provider-errors-view",
                  "spanlight-release-failure-categories-view",
                  "spanlight-spend-chart-view",
                  "spanlight-budget-burn-view",
                  "spanlight-gateway-requests-view",
                  "spanlight-overview-calls-view",
                  "spanlight-incident-overlay",
                  "spanlight.density",
                  "spanlight.columns.<table>",
                ],
                "localStorage",
                "Chart and table view choices",
                "Until you clear them",
              ],
              [
                ["spanlight-verify-banner-dismissed"],
                "sessionStorage",
                "Hides the email verification banner after you dismiss it",
                "Until you close the tab",
              ],
              [
                ["spanlight-oauth-provider"],
                "sessionStorage",
                "Remembers which sign-in button you pressed, to word an error",
                "Until you close the tab",
              ],
              [
                ["spl_invite_token"],
                "sessionStorage",
                "Keeps an invite link's token while you sign in",
                "Until the invite is accepted or found unusable, or you close the tab",
              ],
              [
                ["spanlight-stale-chunk-reload"],
                "sessionStorage",
                "Reloads the page once after an update",
                "Until you close the tab",
              ],
            ] as const
          ).map(([names, ...rest]) => [
            names.map((name) => (
              <code key={name} className="block text-foreground">
                {name}
              </code>
            )),
            ...rest,
          ])}
        />
        <p>We use no analytics, no advertising, and no third-party scripts or fonts.</p>
      </LegalSection>

      <LegalSection id="not-done" title={SECTIONS["not-done"]}>
        <LegalList>
          <li>We do not sell personal data or share it for advertising.</li>
          <li>We make no automated decisions with legal or similar effect (Art. 22 GDPR).</li>
          <li>We do not track you.</li>
        </LegalList>
      </LegalSection>

      <LegalSection id="llm" title={SECTIONS.llm}>
        <p>
          Gateway calls go to the model provider your organisation configures, on your
          organisation&apos;s own credentials. &ldquo;Explain with Claude&rdquo; runs only when a
          user clicks it, and sends secret-redacted trace excerpts to Anthropic on your
          organisation&apos;s own Anthropic credential. Evaluation runs with an LLM judge send the
          items being scored to the model provider on your organisation&apos;s own stored
          credential. All of these run under your own agreement with that provider.
        </p>
        <p>
          The public demo sends fixed sample questions on our own key. Demo visitors cannot send
          their own text.
        </p>
      </LegalSection>

      <LegalSection id="rights" title={SECTIONS.rights}>
        <p>
          You have the right of access, rectification, erasure, restriction of processing, data
          portability and objection (Art. 15 to 21 GDPR). Where processing rests on consent, you may
          withdraw it at any time. To use these rights, email {privacyEmail}; we answer within one
          month.
        </p>
        <p>
          You may also complain to a supervisory authority: the one for our federal state,{" "}
          <Fact value={LEGAL_FACTS.federalState} what="federal state" />, which is{" "}
          <Fact
            value={LEGAL_FACTS.supervisoryAuthority?.name ?? null}
            what="supervisory authority"
            href={LEGAL_FACTS.supervisoryAuthority?.url}
          />
          ; or any supervisory authority in the EU.
        </p>
      </LegalSection>

      <LegalSection id="changes" title={SECTIONS.changes}>
        <p>
          When this policy changes, the date at the top changes. We email account holders about
          material changes.
        </p>
      </LegalSection>
    </LegalPage>
  );
}
