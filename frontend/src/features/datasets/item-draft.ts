import type { DatasetItem, DatasetItemInput, DatasetItemUpdate } from "@/lib/api";

import {
  MAX_METADATA_BYTES,
  editorText,
  jsonBytes,
  parseEditorText,
  payloadProblem,
} from "./item-values";

/** What the item editor holds: the texts of its fields and the metadata pairs. Pure. */
export interface ItemDraft {
  input: string;
  expected: string;
  metadata: Record<string, unknown>;
}

export interface DraftProblems {
  input: string | null;
  expected: string | null;
  metadata: string | null;
}

export function draftFor(item: DatasetItem | null): ItemDraft {
  return {
    input: item ? editorText(item.input) : "",
    expected: editorText(item?.expected_output),
    metadata: item?.metadata ?? {},
  };
}

function expectedChanged(item: DatasetItem, draft: ItemDraft): boolean {
  return draft.expected !== editorText(item.expected_output);
}

function metadataChanged(item: DatasetItem, draft: ItemDraft): boolean {
  return JSON.stringify(draft.metadata) !== JSON.stringify(item.metadata);
}

/**
 * The PATCH body: only the fields that changed, so an untouched value keeps its stored type (a
 * number stays a number, a string that looks like JSON stays a string).
 */
export function draftChanges(item: DatasetItem, draft: ItemDraft): DatasetItemUpdate {
  const update: DatasetItemUpdate = {};
  if (expectedChanged(item, draft)) update.expected_output = parseEditorText(draft.expected);
  if (metadataChanged(item, draft)) update.metadata = draft.metadata;
  return update;
}

export function draftNewItem(draft: ItemDraft): DatasetItemInput {
  return {
    input: parseEditorText(draft.input),
    expected_output: parseEditorText(draft.expected),
    metadata: draft.metadata,
  };
}

/** Whether there is anything to save. */
export function draftDirty(item: DatasetItem | null, draft: ItemDraft): boolean {
  if (item === null) return draft.input.trim() !== "";
  return expectedChanged(item, draft) || metadataChanged(item, draft);
}

/** Why a field can't be saved. Only fields that will be sent are checked. */
export function draftProblems(item: DatasetItem | null, draft: ItemDraft): DraftProblems {
  const expectedSent = item === null || expectedChanged(item, draft);
  return {
    input: item === null ? payloadProblem(draft.input) : null,
    expected: expectedSent ? payloadProblem(draft.expected) : null,
    metadata:
      jsonBytes(draft.metadata) > MAX_METADATA_BYTES
        ? "Metadata is larger than 8 KB. Remove some pairs to save."
        : null,
  };
}
