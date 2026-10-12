import { z } from "zod";

import { projectSearchSchema } from "@/lib/time-range";

/**
 * `/playground?prompt=<name>&v=<version>` opens with that prompt version in column A ("Open in
 * playground" on a prompt). Without both, the playground starts empty.
 */
export const playgroundSearchSchema = projectSearchSchema.extend({
  prompt: z.string().min(1).optional().catch(undefined),
  v: z.coerce.number().int().positive().optional().catch(undefined),
});

export type PlaygroundSearch = z.infer<typeof playgroundSearchSchema>;
