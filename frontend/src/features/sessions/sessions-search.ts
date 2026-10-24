import { z } from "zod";

import { projectSearchSchema } from "@/lib/time-range";

/**
 * URL-synced sort, direction and session or user ID search of the sessions list (spec §8). The
 * defaults (`last`, `desc`) stay out of the URL, as on the traces list: absent means default.
 */
export const sessionsSearchSchema = projectSearchSchema.extend({
  sort: z.enum(["last", "cost", "turns", "errors", "tokens"]).optional().catch(undefined),
  order: z.enum(["asc", "desc"]).optional().catch(undefined),
  q: z.string().min(1).max(256).optional().catch(undefined),
});

export type SessionsSearch = z.infer<typeof sessionsSearchSchema>;
