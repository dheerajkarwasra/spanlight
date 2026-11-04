import { Link } from "@tanstack/react-router";

import { DOCS_LINKS, LEGAL_PATHS } from "@/features/landing/links";

import { LEGAL_FACTS } from "../legal-facts";
import { LegalPage } from "../legal-page";
import { BackupsUnconfirmed, LegalList, LegalSection } from "../legal-parts";

/** The terms version; sub-project 2 records it when a user accepts the terms at signup. */
export const VERSION = "2026-11-01";

const SECTIONS = {
  who: "Who may use Spanlight",
  service: "The service",
  "acceptable-use": "Acceptable use",
  content: "Your content",
  providers: "Third-party providers",
  ending: "Suspension and ending",
  liability: "Liability",
  changes: "Changes to these terms",
  law: "Law and venue",
  "open-source": "Open source",
} as const;

const CONTENTS = Object.entries(SECTIONS).map(([id, title]) => ({ id, title }));

const mailto = (email: string) => <a href={`mailto:${email}`}>{email}</a>;

/** Terms for the hosted service at spanlight.dev: business use only, German law. */
export function TermsPage() {
  return (
    <LegalPage title="Terms of service" version={VERSION} contents={CONTENTS}>
      <p>
        These terms are a contract between you and {LEGAL_FACTS.operatorName}, who runs the hosted
        Spanlight service at spanlight.dev (see the <Link to={LEGAL_PATHS.imprint}>Imprint</Link>).
        By creating an account you accept them. Questions go to {mailto(LEGAL_FACTS.legalEmail)}.
      </p>

      <LegalSection id="who" title={SECTIONS.who}>
        <p>
          Spanlight is for business use only. You may use it if you are a business or a person
          acting for your trade or profession (§14 BGB), you are 18 or over, and your account
          details are accurate. If you sign up for a company, you confirm that you may bind it to
          these terms.
        </p>
      </LegalSection>

      <LegalSection id="service" title={SECTIONS.service}>
        <p>
          The service is free and has not reached version 1.0. We make no promise of availability.
          Features may change, and we may end the service with 30 days&apos; notice by email.
        </p>
      </LegalSection>

      <LegalSection id="acceptable-use" title={SECTIONS["acceptable-use"]}>
        <p>You must not:</p>
        <LegalList>
          <li>use the service for anything illegal, infringing or harmful;</li>
          <li>
            use the gateway as an open relay, or to reach targets you are not entitled to use;
          </li>
          <li>run load or denial-of-service tests against the service;</li>
          <li>
            do security research beyond what our{" "}
            <a href={DOCS_LINKS.securityPolicy}>security policy</a> allows;
          </li>
          <li>
            send special categories of personal data (GDPR Art. 9: health, biometric, genetic and
            similar data), or any data that needs HIPAA protection;
          </li>
          <li>
            exceed the documented <a href={`${DOCS_LINKS.limits}#rate-limits`}>rate limits</a>.
          </li>
        </LegalList>
        <p>Report abuse to {mailto(LEGAL_FACTS.abuseEmail)}.</p>
      </LegalSection>

      <LegalSection id="content" title={SECTIONS.content}>
        <p>
          You keep all rights in the traces and other content you send. You grant us only what we
          need to run the service for you. You are responsible for having a legal basis for what you
          send, including toward your own end users. The{" "}
          <Link to={LEGAL_PATHS.dpa}>Data processing agreement</Link> is part of these terms.
        </p>
      </LegalSection>

      <LegalSection id="providers" title={SECTIONS.providers}>
        <p>
          Gateway and evaluation calls go to the model provider you configure, with your own
          credentials, under your own agreement with that provider.
        </p>
      </LegalSection>

      <LegalSection id="ending" title={SECTIONS.ending}>
        <p>
          You may delete your organisation at any time in its settings, and your account in Settings
          → Account unless you are the only owner of an organisation. We may suspend your access if
          you breach the acceptable use rules, or end the contract with 30 days&apos; notice.
        </p>
        <p>
          When you delete an organisation, its data is removed at once, and backups age out within
          about 8 weeks.
          <BackupsUnconfirmed /> Export what you want to keep first, with the exports API.
        </p>
      </LegalSection>

      <LegalSection id="liability" title={SECTIONS.liability}>
        <LegalList>
          <li>
            We are liable without limit for intent and gross negligence, for injury to life, body or
            health, and under the Product Liability Act.
          </li>
          <li>
            For slight negligence we are liable only for breach of an essential contractual duty,
            and only up to the typical, foreseeable damage.
          </li>
          <li>Otherwise our liability is excluded, as far as the law allows for a free service.</li>
        </LegalList>
      </LegalSection>

      <LegalSection id="changes" title={SECTIONS.changes}>
        <p>
          We tell you about changes to these terms by email at least 30 days before they take
          effect, and you may object. If you object, either side may end the contract.
        </p>
      </LegalSection>

      <LegalSection id="law" title={SECTIONS.law}>
        <p>
          German law applies, without the UN Convention on Contracts for the International Sale of
          Goods. Where §38 ZPO permits, the place of jurisdiction is the operator&apos;s place of
          residence.
        </p>
      </LegalSection>

      <LegalSection id="open-source" title={SECTIONS["open-source"]}>
        <p>
          The Spanlight code is open source under the MIT licence, which governs the code. These
          terms govern only the hosted service.
        </p>
      </LegalSection>
    </LegalPage>
  );
}
