import { Link } from "@tanstack/react-router";

import { LEGAL_PATHS } from "@/features/landing/links";

import { LEGAL_FACTS } from "../legal-facts";
import { LegalPage } from "../legal-page";
import { LegalList, LegalSection, LegalTable, Missing } from "../legal-parts";

export const VERSION = "2026-11-01";

/** The providers that process customer data for the hosted service. Confirmed rows only. */
export function SubprocessorsPage() {
  return (
    <LegalPage title="Subprocessors" version={VERSION}>
      <p>
        These providers process customer data on our behalf to run the hosted Spanlight service.
      </p>

      <LegalSection id="current" title="Current subprocessors">
        <LegalTable
          label="Subprocessors"
          head={["Provider", "Purpose", "Data", "Location"]}
          rows={LEGAL_FACTS.confirmedSubprocessors.map((s) => [
            <span className="font-semibold text-foreground">{s.name}</span>,
            s.purpose,
            s.data,
            s.location,
          ])}
        />
        <p>
          We are completing this list. <Missing what="remaining subprocessors" />
        </p>
      </LegalSection>

      <LegalSection id="other-providers" title="Other providers">
        <LegalList>
          <li>
            <span className="text-foreground">Cloudflare</span> handles DNS for spanlight.dev only.
            It receives no customer data and is not a subprocessor.
          </li>
          <li>
            <span className="text-foreground">Anthropic</span> receives the public demo&apos;s fixed
            sample questions, sent on our own API key. No customer data is sent. &ldquo;Explain with
            Claude&rdquo; runs on your organisation&apos;s own provider key, under your own
            agreement with that provider.
          </li>
        </LegalList>
      </LegalSection>

      <LegalSection id="changes" title="Changes to this list">
        <p>
          We give at least 30 days&apos; notice before a new subprocessor starts processing customer
          data, on this page and by email to organisation owners. You may object and end the
          contract, as the <Link to={LEGAL_PATHS.dpa}>Data processing agreement</Link> describes.
        </p>
        <p>Version {VERSION}</p>
      </LegalSection>
    </LegalPage>
  );
}
