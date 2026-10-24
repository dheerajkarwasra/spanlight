import { z } from "zod";

import { projectSearchSchema } from "@/lib/time-range";

/**
 * URL-synced sort, direction and user ID search of the end-user list. The defaults (`cost`,
 * `desc`) stay out of the URL, as on the traces list: absent means default.
 */
export const usersSearchSchema = projectSearchSchema.extend({
  sort: z.enum(["cost", "errors", "traces"]).optional().catch(undefined),
  order: z.enum(["asc", "desc"]).optional().catch(undefined),
  q: z.string().min(1).max(256).optional().catch(undefined),
});

export type UsersSearch = z.infer<typeof usersSearchSchema>;
