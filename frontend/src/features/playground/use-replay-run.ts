import { useState } from "react";

import type { ReplayInput } from "@/lib/api";

import { outcomeOfError, outcomeOfResult, type ReplayOutcome } from "./replay-outcome";
import { useRunReplay } from "./replay-queries";

/** Runs a replay and keeps its outcome until the next run. */
export function useReplayRun() {
  const mutation = useRunReplay();
  const [outcome, setOutcome] = useState<ReplayOutcome>({ status: "idle" });

  async function run(input: ReplayInput) {
    setOutcome({ status: "running" });
    try {
      // A fresh key per click: one click is one call, a retry of it is not a second one.
      const result = await mutation.mutateAsync({ input, key: crypto.randomUUID() });
      setOutcome(outcomeOfResult(result));
    } catch (error) {
      setOutcome(outcomeOfError(error));
    }
  }

  return { outcome, running: mutation.isPending, run };
}
