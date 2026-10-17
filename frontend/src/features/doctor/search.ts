import { z } from "zod";

import { projectSearchSchema } from "@/lib/time-range";

/**
 * URL-synced state of the Doctor page: the insights list or the Precision tab, and the list's
 * status tab, severity and kind.
 */
export const doctorSearchSchema = projectSearchSchema.extend({
  tab: z.enum(["insights", "precision"]).default("insights").catch("insights"),
  status: z.enum(["open", "acknowledged", "muted", "resolved"]).default("open").catch("open"),
  severity: z.enum(["info", "warning", "critical"]).optional().catch(undefined),
  kind: z.string().optional().catch(undefined),
});

export type DoctorSearch = z.infer<typeof doctorSearchSchema>;
