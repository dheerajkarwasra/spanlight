import { useCallback, useState } from "react";

import type { ScoreConfig } from "@/lib/api";

import { emptyDraft, type ScoreDraft } from "./score-draft";

interface DraftState {
  itemId: string | null;
  values: ScoreDraft;
  comment: string;
  /** The score that last held focus; keys `1`–`9` act on it. */
  focused: string | null;
}

/**
 * The unsaved scores and comment for the item on screen. A new item starts from an empty draft:
 * the state remembers which item it belongs to, so nothing carries over between items.
 */
export function useItemDraft(itemId: string | null, configs: readonly ScoreConfig[]) {
  const [state, setState] = useState<DraftState>({
    itemId: null,
    values: {},
    comment: "",
    focused: null,
  });
  const current: DraftState =
    state.itemId === itemId
      ? state
      : { itemId, values: emptyDraft(configs), comment: "", focused: null };

  const update = useCallback(
    (change: (draft: DraftState) => Partial<DraftState>) => {
      setState((previous) => {
        const base: DraftState =
          previous.itemId === itemId
            ? previous
            : { itemId, values: emptyDraft(configs), comment: "", focused: null };
        return { ...base, ...change(base) };
      });
    },
    [configs, itemId],
  );

  return {
    itemId,
    values: current.values,
    comment: current.comment,
    focused: current.focused,
    setValue: (name: string, value: string | null) =>
      update((draft) => ({ values: { ...draft.values, [name]: value } })),
    setComment: (comment: string) => update(() => ({ comment })),
    setFocused: (name: string | null) => update(() => ({ focused: name })),
  };
}
