import type { CompareStep } from "@/lib/api";

/** What happened to a step between trace A and trace B, most telling first. */
export type StepState =
  | "only_a"
  | "only_b"
  | "failed_a"
  | "failed_b"
  | "output_changed"
  | "same_output"
  | "output_unknown";

export function stepState(step: CompareStep): StepState {
  if (step.b === null) {
    return "only_a";
  }
  if (step.a === null) {
    return "only_b";
  }
  // A step that fails on one side only is the change to look at first.
  if (step.b.status === "error" && step.a.status !== "error") {
    return "failed_b";
  }
  if (step.a.status === "error" && step.b.status !== "error") {
    return "failed_a";
  }
  if (step.output_changed === null) {
    return "output_unknown";
  }
  return step.output_changed ? "output_changed" : "same_output";
}

/** False only for a step on both sides with the same output, input, model and numbers. */
export function stepChanged(step: CompareStep): boolean {
  const state = stepState(step);
  if (state !== "same_output" && state !== "output_unknown") {
    return true;
  }
  return (
    step.input_changed === true ||
    step.a?.model !== step.b?.model ||
    [step.input_tokens, step.output_tokens].some((delta) => delta !== null && delta !== 0) ||
    (step.cost_usd !== null && Number(step.cost_usd) !== 0)
  );
}
