import { LEGAL_FACTS } from "../legal-facts";
import { LegalPage } from "../legal-page";
import { Fact, LegalSection } from "../legal-parts";

export const VERSION = "2026-11-01";

/** Provider identification under §5 DDG and §18(2) MStV. */
export function ImprintPage() {
  return (
    <LegalPage title="Imprint" version={VERSION}>
      <p>Impressum: information about the provider of spanlight.dev under §5 DDG.</p>

      <LegalSection id="provider" title="Provider">
        <p className="text-foreground">{LEGAL_FACTS.operatorName}</p>
        <p>
          <span className="text-foreground">Postal address:</span>{" "}
          <Fact value={LEGAL_FACTS.postalAddress} what="postal address" />
        </p>
        <p>
          <span className="text-foreground">Email:</span>{" "}
          <a href={`mailto:${LEGAL_FACTS.legalEmail}`}>{LEGAL_FACTS.legalEmail}</a>
        </p>
      </LegalSection>

      <LegalSection id="responsible" title="Responsible for content">
        <p>Responsible for content under §18(2) MStV: {LEGAL_FACTS.operatorName}</p>
      </LegalSection>
    </LegalPage>
  );
}
