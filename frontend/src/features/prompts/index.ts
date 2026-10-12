/**
 * What other features use from `features/prompts`: the shared prompt reads (pickers and a
 * version by id) and the search schemas. The router imports `search.ts` and the pages directly,
 * so the pages stay lazy.
 */
export {
  PROMPT_PICKER_LIMIT,
  promptPickerQuery,
  promptVersionByIdQuery,
  promptVersionsPickerQuery,
  usePromptVersionByIdQuery,
} from "./prompt-query-options";
export {
  promptDetailSearchSchema,
  promptsSearchSchema,
  type PromptDetailSearch,
  type PromptsSearch,
} from "./search";
