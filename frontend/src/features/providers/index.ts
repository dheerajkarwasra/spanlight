/**
 * What other features may use from `features/providers`. The page itself is loaded by the router
 * from `providers-page.tsx`; the incident overlay shares `lib/incident-bands.ts`, not this folder.
 */
export { useProviderIncidents } from "./providers-queries";
