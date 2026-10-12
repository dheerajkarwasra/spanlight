import type { PromptListItem } from "@/lib/api";
import { formatPromptVersion } from "@/lib/format";

export interface PromptOption {
  /** `<version>|<name>`: a name may contain anything a path may, but never `|`. */
  value: string;
  label: string;
  name: string;
  version: number;
}

export function optionValue(name: string, version: number): string {
  return `${version}|${name}`;
}

export function parseOptionValue(value: string): { name: string; version: number } | null {
  const bar = value.indexOf("|");
  const version = Number(value.slice(0, bar));
  if (bar < 1 || !Number.isInteger(version)) {
    return null;
  }
  return { name: value.slice(bar + 1), version };
}

/**
 * One option per labelled version of each prompt, plus its latest: "support/answer v3 · staging".
 * A version with several labels is one option naming all of them.
 */
export function promptOptions(items: readonly PromptListItem[]): PromptOption[] {
  return items.flatMap((item) => {
    const labelsByVersion = new Map<number, string[]>([[item.latest_version, ["latest"]]]);
    for (const { label, version } of item.labels) {
      labelsByVersion.set(version, [...(labelsByVersion.get(version) ?? []), label]);
    }
    return [...labelsByVersion.entries()]
      .sort(([a], [b]) => b - a)
      .map(([version, labels]) => ({
        value: optionValue(item.name, version),
        label: `${formatPromptVersion(item.name, version)} · ${labels.join(", ")}`,
        name: item.name,
        version,
      }));
  });
}
