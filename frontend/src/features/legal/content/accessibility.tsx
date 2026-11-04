import { LEGAL_FACTS } from "../legal-facts";
import { LegalPage } from "../legal-page";
import { LegalList, LegalSection } from "../legal-parts";

export const VERSION = "2026-11-01";

const SECTIONS = {
  commitment: "1. Commitment",
  status: "2. Status",
  "how-we-test": "3. How we test",
  feedback: "4. Feedback",
  "date-of-this-statement": "5. Date of this statement",
} as const;

const CONTENTS = Object.entries(SECTIONS).map(([id, title]) => ({ id, title }));

/** Accessibility statement for spanlight.dev: only what our own tests show, no audit claimed. */
export function AccessibilityPage() {
  return (
    <LegalPage title="Accessibility" version={VERSION} contents={CONTENTS}>
      <p>This statement covers the Spanlight website and app at spanlight.dev.</p>

      <LegalSection id="commitment" title={SECTIONS.commitment}>
        <p>
          We want everyone to be able to use Spanlight, including people who use a keyboard, a
          screen reader or zoom. Our target is the Web Content Accessibility Guidelines (WCAG) 2.2,
          level AA.
        </p>
      </LegalSection>

      <LegalSection id="status" title={SECTIONS.status}>
        <p>
          Spanlight is partially conformant with WCAG 2.2 level AA: some parts do not fully meet the
          standard yet. Known limitations:
        </p>
        <LegalList>
          <li>No independent audit has been done yet.</li>
        </LegalList>
      </LegalSection>

      <LegalSection id="how-we-test" title={SECTIONS["how-we-test"]}>
        <p>We check accessibility in two ways:</p>
        <LegalList>
          <li>
            Automated checks with axe, run in our Playwright end-to-end tests on the landing page,
            sign-up and sign-in, onboarding, the main dashboard screens and the legal pages.
          </li>
          <li>
            Keyboard checks in the same tests, such as shortcuts and closing dialogs with Escape.
          </li>
        </LegalList>
        <p>
          Automated tools find only some kinds of problems, so we want to hear about anything they
          miss.
        </p>
      </LegalSection>

      <LegalSection id="feedback" title={SECTIONS.feedback}>
        <p>
          If something on Spanlight is hard to use, or does not work with your assistive technology,
          please tell us which page it was and what happened.
        </p>
        <p>
          <span className="text-foreground">Contact:</span>{" "}
          <a href={`mailto:${LEGAL_FACTS.legalEmail}`}>{LEGAL_FACTS.legalEmail}</a>
        </p>
      </LegalSection>

      <LegalSection id="date-of-this-statement" title={SECTIONS["date-of-this-statement"]}>
        <p>
          This statement was prepared on <time dateTime={VERSION}>{VERSION}</time>. It is based on
          our own testing, not on an independent audit.
        </p>
      </LegalSection>
    </LegalPage>
  );
}
