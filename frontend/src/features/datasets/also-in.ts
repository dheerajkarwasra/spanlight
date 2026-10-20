import type { TracesAdded } from "@/lib/api";

export type AlsoIn = TracesAdded["also_in"];

/** "A", "A and B", "A, B and C": the other datasets' names, each once, in first-seen order. */
export function alsoInNames(alsoIn: AlsoIn, quoted = false): string {
  const byId = new Map(
    alsoIn.flatMap((entry) => entry.datasets.map((d) => [d.id, d.name] as const)),
  );
  const names = [...byId.values()].map((name) => (quoted ? `“${name}”` : name));
  return names.length > 1
    ? `${names.slice(0, -1).join(", ")} and ${names.at(-1) ?? ""}`
    : (names[0] ?? "");
}
