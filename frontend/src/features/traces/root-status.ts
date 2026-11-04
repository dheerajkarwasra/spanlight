/**
 * Why a trace can't be called OK without errors: its root span hasn't arrived (`false`), or
 * whether it has is unknown (`null`). Null for a trace whose root was received.
 */
export function rootStatusLabel(rootReceived: boolean | null): string | null {
  if (rootReceived === true) {
    return null;
  }
  return rootReceived === false ? "Root span not received" : "Root status unavailable";
}
