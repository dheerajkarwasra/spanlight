import { z } from "zod";

import { projectSearchSchema } from "@/lib/time-range";

/**
 * `/playground?prompt=<name>&v=<version>` opens with that prompt version in column A ("Open in
 * playground" on a prompt). Without both, the playground starts empty.
 *
 * `/playground?replay=<trace_id>:<span_id>` opens the replay column for that span instead of the
 * run columns ("Replay" in the span panel); `edit` opens its request already in edit mode.
 */
export const playgroundSearchSchema = projectSearchSchema.extend({
  prompt: z.string().min(1).optional().catch(undefined),
  v: z.coerce.number().int().positive().optional().catch(undefined),
  replay: z
    .string()
    .regex(/^[0-9a-f]{32}:[0-9a-f]{16}$/)
    .optional()
    .catch(undefined),
  edit: z.coerce.boolean().optional().catch(undefined),
});

export type PlaygroundSearch = z.infer<typeof playgroundSearchSchema>;
