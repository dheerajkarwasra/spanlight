import { z } from "zod";

import { projectSearchSchema } from "@/lib/time-range";

/** The run pair, `a` the baseline and `b` the candidate: ticked on the detail page, compared on the compare page. */
export const experimentSearchSchema = projectSearchSchema.extend({
  // Run ids are UUIDs; anything else is dropped rather than sent to the API.
  a: z.uuid().optional().catch(undefined),
  b: z.uuid().optional().catch(undefined),
});

/** The compare page adds the score that sorts the items. */
export const compareSearchSchema = experimentSearchSchema.extend({
  score: z.string().optional().catch(undefined),
});

/** A new experiment prefilled from a dataset, a prompt version or a playground column. */
export const newExperimentSearchSchema = projectSearchSchema.extend({
  dataset: z.uuid().optional().catch(undefined),
  prompt: z.string().min(1).max(256).optional().catch(undefined),
  version: z.uuid().optional().catch(undefined),
});

export type ExperimentSearch = z.infer<typeof experimentSearchSchema>;
export type CompareSearch = z.infer<typeof compareSearchSchema>;
export type NewExperimentSearch = z.infer<typeof newExperimentSearchSchema>;
