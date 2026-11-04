import { Link } from "@tanstack/react-router";

import { LEGAL_PATHS } from "@/features/landing/links";
import { docsLink } from "@/lib/docs-links";

import { LEGAL_FACTS } from "../legal-facts";
import { LegalPage } from "../legal-page";
import { BackupsUnconfirmed, Fact, LegalList, LegalSection } from "../legal-parts";

export const VERSION = "2026-11-01";

const SECTIONS = {
  subject: "Subject matter and duration",
  nature: "Nature and purpose",
  data: "Kinds of personal data",
  subjects: "Categories of data subjects",
  instructions: "Instructions",
  confidentiality: "Confidentiality",
  security: "Security measures (Annex 1)",
  subprocessors: "Subprocessors",
  assistance: "Help with data-subject rights",
  breaches: "Personal data breaches",
  end: "End of processing",
  audits: "Information and audits",
  transfers: "Transfers",
} as const;

const CONTENTS = Object.entries(SECTIONS).map(([id, title]) => ({ id, title }));

const privacyEmail = <a href={`mailto:${LEGAL_FACTS.privacyEmail}`}>{LEGAL_FACTS.privacyEmail}</a>;

/** Art. 28(3) GDPR agreement for customer content; part of the terms of service. */
export function DpaPage() {
  return (
    <LegalPage title="Data processing agreement" version={VERSION} contents={CONTENTS}>
      <p>
        This agreement under Art. 28(3) GDPR is between you, the customer, as controller, and{" "}
        {LEGAL_FACTS.operatorName} as processor. It covers the personal data in the traces and other
        content you send to the hosted Spanlight service. It forms part of the{" "}
        <Link to={LEGAL_PATHS.terms}>Terms of service</Link>, so accepting the terms accepts it.
      </p>

      <LegalSection id="subject" title={SECTIONS.subject}>
        <p>We host and process your telemetry for you, for as long as your organisation exists.</p>
      </LegalSection>

      <LegalSection id="nature" title={SECTIONS.nature}>
        <p>
          We store, display, analyse and delete your traces, and send on the gateway requests you
          send us. &ldquo;Explain with Claude&rdquo; runs only when one of your users clicks it, and
          then sends secret-redacted trace excerpts to Anthropic on your organisation&apos;s own
          Anthropic credential. Evaluation runs with an LLM judge send the items being scored to the
          model provider on your organisation&apos;s own stored credential.
        </p>
      </LegalSection>

      <LegalSection id="data" title={SECTIONS.data}>
        <LegalList>
          <li>Whatever your prompts, completions and metadata contain.</li>
          <li>End-user ids and session ids.</li>
          <li>Your staff&apos;s account data, where we process it for you.</li>
        </LegalList>
      </LegalSection>

      <LegalSection id="subjects" title={SECTIONS.subjects}>
        <p>Your end users and your staff.</p>
      </LegalSection>

      <LegalSection id="instructions" title={SECTIONS.instructions}>
        <p>
          We process the data only on your documented instructions. They are these terms; the
          settings you choose in the product, such as the payload capture switch, redaction,
          retention and end-user erasure; and further instructions you send by email to{" "}
          {privacyEmail}.
        </p>
      </LegalSection>

      <LegalSection id="confidentiality" title={SECTIONS.confidentiality}>
        <p>Everyone with access to the data is bound to confidentiality.</p>
      </LegalSection>

      <LegalSection id="security" title={SECTIONS.security}>
        <p>We take these technical and organisational measures (Art. 32 GDPR):</p>
        <LegalList>
          <li>
            Passwords hashed with argon2id; server-side sessions; CSRF and Origin checks; two-factor
            authentication, which an organisation can require.
          </li>
          <li>Row-level security in the database between tenants.</li>
          <li>AES-256-GCM encryption of stored secrets, such as provider credentials.</li>
          <li>TLS in transit, with HSTS.</li>
          <li>A hash-chained audit log.</li>
          <li>Secret redaction on ingest, and retention you configure per project.</li>
          <li>
            Nightly database backups.
            <BackupsUnconfirmed />
          </li>
          <li>Dependency and container image scanning; signed releases.</li>
          <li>
            Encryption at rest of the database volume and backups:{" "}
            <Fact value={LEGAL_FACTS.encryptionAtRest} what="encryption at rest" />
          </li>
        </LegalList>
      </LegalSection>

      <LegalSection id="subprocessors" title={SECTIONS.subprocessors}>
        <p>
          You give us general authorisation to use subprocessors. The current list is on the{" "}
          <Link to={LEGAL_PATHS.subprocessors}>Subprocessors</Link> page. We give at least 30
          days&apos; notice of a new subprocessor, on that page and by email to organisation owners.
          You may object, and end the contract.
        </p>
      </LegalSection>

      <LegalSection id="assistance" title={SECTIONS.assistance}>
        <p>
          To answer your end users&apos; requests, you can use the end-user erasure and export APIs.
          We also help by email at {privacyEmail}.
        </p>
      </LegalSection>

      <LegalSection id="breaches" title={SECTIONS.breaches}>
        <p>
          We notify you of a personal data breach without undue delay, aiming for 48 hours after we
          become aware, with the information Art. 33(3) GDPR asks for.
        </p>
      </LegalSection>

      <LegalSection id="end" title={SECTIONS.end}>
        <p>
          When you delete an organisation or a project, its data is deleted, and backups age out
          within about 8 weeks.
          <BackupsUnconfirmed /> You can export your data before you delete it.
        </p>
      </LegalSection>

      <LegalSection id="audits" title={SECTIONS.audits}>
        <p>
          On request we provide the documentation that shows we meet this agreement: this page, the{" "}
          <a href={docsLink("security/threat-model/")}>threat model</a> and the{" "}
          <a href={docsLink("security/asvs-l2-checklist/")}>ASVS checklist</a>. You may carry out
          reasonable audits at your own cost, with notice.
        </p>
      </LegalSection>

      <LegalSection id="transfers" title={SECTIONS.transfers}>
        <p>
          Hosting is in the EU: {LEGAL_FACTS.hosting}. Railway is a US company. Any access to data
          from the US rests on Railway&apos;s data processing agreement with standard contractual
          clauses, or on the EU-US Data Privacy Framework.
        </p>
      </LegalSection>
    </LegalPage>
  );
}
