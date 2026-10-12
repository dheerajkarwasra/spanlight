import { useState } from "react";

let next = 0;

function newKey(): string {
  next += 1;
  return `item-${String(next)}`;
}

/**
 * Stable React keys for an editable list whose items have no id of their own (chat messages).
 * Keying by index would move a removed row's state (an open select, focus) onto its neighbour.
 * Call `added` / `removed(index)` next to the change of the list itself.
 */
export function useListKeys(count: number) {
  const [keys, setKeys] = useState(() => Array.from({ length: count }, newKey));
  // A list replaced from outside (another length) gets keys for its new tail.
  const current =
    keys.length === count
      ? keys
      : Array.from({ length: count }, (_, at) => keys[at] ?? `tail-${String(at)}`);
  return {
    keys: current,
    added: () => setKeys([...current, newKey()]),
    removed: (index: number) => setKeys(current.filter((_, at) => at !== index)),
  };
}
