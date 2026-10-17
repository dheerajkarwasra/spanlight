import { z } from "zod";

import { projectSearchSchema } from "@/lib/time-range";

/**
 * The reconciliation page's search params: the window's first and last UTC day, inclusive
 * (`?first=2026-10-01&last=2026-10-14`). They are not the project's `from`/`to`, which belong to
 * the time range the other pages share and would leak into it.
 */
export const reconciliationSearchSchema = projectSearchSchema.extend({
  first: z.string().optional().catch(undefined),
  last: z.string().optional().catch(undefined),
});

export type ReconciliationSearch = z.infer<typeof reconciliationSearchSchema>;
