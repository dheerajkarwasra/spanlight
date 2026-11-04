/**
 * The single place for operator facts used by the legal pages. An unknown fact is `null` and
 * renders through `Missing`, so a review cannot overlook it and nothing is guessed.
 */

export interface Subprocessor {
  name: string;
  purpose: string;
  data: string;
  location: string;
}

export const LEGAL_FACTS: {
  operatorName: string;
  postalAddress: string | null;
  federalState: string | null;
  supervisoryAuthority: { name: string; url: string } | null;
  privacyEmail: string;
  legalEmail: string;
  abuseEmail: string;
  hosting: string;
  railwayLogRetention: string | null;
  encryptionAtRest: string | null;
  backupsEnabled: boolean | null;
  /** Only providers the owner has confirmed. Others appear once confirmed, never before. */
  confirmedSubprocessors: readonly Subprocessor[];
} = {
  operatorName: "Dheeraj Karwasra",
  postalAddress: null,
  federalState: null,
  supervisoryAuthority: null,
  privacyEmail: "privacy@spanlight.dev",
  legalEmail: "legal@spanlight.dev",
  abuseEmail: "abuse@spanlight.dev",
  hosting: "Railway, EU West (Amsterdam)",
  railwayLogRetention: null,
  encryptionAtRest: null,
  // The owner confirms that BACKUPS_ENABLED is set on Railway before this becomes true.
  backupsEnabled: null,
  confirmedSubprocessors: [
    {
      name: "Railway Corporation",
      purpose: "Hosting, database and logs",
      data: "All service data",
      location: "EU West (Amsterdam); US company",
    },
  ],
};
