import { useStoredChoice } from "@/lib/use-stored-choice";

const RAIL_STATES = ["expanded", "collapsed"] as const;

/** Whether the viewer collapsed the desktop rail to icons, remembered per browser. */
export function useRailCollapsed(): [boolean, (next: boolean) => void] {
  const [state, setState] = useStoredChoice("spanlight.rail", RAIL_STATES, "expanded");
  return [
    state === "collapsed",
    (next) => {
      setState(next ? "collapsed" : "expanded");
    },
  ];
}
