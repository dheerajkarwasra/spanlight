import { useStoredChoice, useStoredValue } from "@/lib/use-stored-choice";

export type Density = "comfortable" | "compact";

const DENSITIES: readonly Density[] = ["comfortable", "compact"];

/** Row heights (`h-10` / `h-8`): the virtualizer's stride. */
export const ROW_PX: Record<Density, number> = { comfortable: 40, compact: 32 };
/** The header row is `h-9`; the box's `scroll-pt-9` and the virtualizer's margin match it. */
export const HEADER_PX = 36;

/** "name:1,model:0" → { name: true, model: false }; anything malformed is skipped. */
function parseOverrides(stored: string): Record<string, boolean> {
  const overrides: Record<string, boolean> = {};
  for (const entry of stored.split(",")) {
    const at = entry.lastIndexOf(":");
    const flag = entry.slice(at + 1);
    if (at > 0 && (flag === "1" || flag === "0")) {
      overrides[entry.slice(0, at)] = flag === "1";
    }
  }
  return overrides;
}

function serializeOverrides(overrides: Record<string, boolean>): string {
  return Object.entries(overrides)
    .map(([id, visible]) => `${id}:${visible ? "1" : "0"}`)
    .join(",");
}

/**
 * The viewer's table choices: one density for every data table (`spanlight.density`) and the
 * shown or hidden columns of this table (`spanlight.columns.<id>`).
 */
export function useTablePrefs(id: string) {
  const [density, setDensity] = useStoredChoice("spanlight.density", DENSITIES, "comfortable");
  const [stored, setStored] = useStoredValue(`spanlight.columns.${id}`, "");
  const overrides = parseOverrides(stored);

  function setVisible(colId: string, visible: boolean) {
    setStored(serializeOverrides({ ...overrides, [colId]: visible }));
  }

  return { density, setDensity, overrides, setVisible };
}
