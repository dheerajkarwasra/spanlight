import { z } from "zod";

import { projectSearchSchema } from "@/lib/time-range";

/** The metadata key pattern the API accepts; any other key would only earn a 422. */
const METADATA_KEY = /^[a-z0-9_.-]{1,64}$/;

/**
 * URL-synced view of the Spend page, on top of the shell's window and environment params.
 * `key` names the metadata key and is dropped for every other `group_by`, which the API refuses
 * it with. `focus` is the group the chart shows (spec ruling 10); the page drops it when the
 * grouping changes.
 */
export const attributionSearchSchema = projectSearchSchema
  .extend({
    group_by: z
      .enum(["metadata", "trace_name", "user", "release"])
      .default("trace_name")
      .catch("trace_name"),
    key: z.string().regex(METADATA_KEY).optional().catch(undefined),
    sort: z.enum(["cost", "calls", "errors", "retry_cost"]).default("cost").catch("cost"),
    // The router reads an all-digit value as a number.
    focus: z.coerce.string().min(1).max(512).optional().catch(undefined),
  })
  .transform((search) => (search.group_by === "metadata" ? search : { ...search, key: undefined }));

export type AttributionSearch = z.infer<typeof attributionSearchSchema>;
