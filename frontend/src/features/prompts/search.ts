import { z } from "zod";

import { projectSearchSchema } from "@/lib/time-range";

const versionNumber = z.coerce.number().int().positive().optional().catch(undefined);

/** `/prompts`: `create` opens the new-prompt editor in place of the list. */
export const promptsSearchSchema = projectSearchSchema.extend({
  create: z.boolean().optional().catch(undefined),
});

/**
 * `/prompts/$name`. `view` picks the screen: the versions (default), the editor for a new version
 * or the diff. `v` is the version shown (and the editor's starting point); `a` and `b` are the
 * older and newer versions of the diff (`from`/`to` already name the custom time range).
 */
export const promptDetailSearchSchema = projectSearchSchema.extend({
  view: z.enum(["versions", "edit", "diff"]).optional().catch(undefined),
  v: versionNumber,
  a: versionNumber,
  b: versionNumber,
});

export type PromptsSearch = z.infer<typeof promptsSearchSchema>;
export type PromptDetailSearch = z.infer<typeof promptDetailSearchSchema>;
