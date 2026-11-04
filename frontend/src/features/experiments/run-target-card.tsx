import type { ReactNode } from "react";

import { SectionCard } from "@/components/section-card";
import { TemplateBlocks } from "@/features/prompts";
import type { ExperimentTarget } from "@/lib/api";
import { pluralize } from "@/lib/format";

import { paramsLabel, targetLabel, type VersionIndex } from "./run-label";

/**
 * Figma "Experiments — Run — ran with (G-003)": what the run sent for every item, so two runs
 * that differ only in a parameter no longer look the same.
 */
export function RunTargetCard({
  target,
  index,
}: {
  target: ExperimentTarget;
  index: VersionIndex;
}) {
  return (
    <SectionCard
      title="Ran with"
      description="What this run sent for every item. A parameter left unset uses the provider's default."
    >
      <dl className="grid gap-x-4 gap-y-2.5 text-sm sm:grid-cols-[140px_1fr]">
        <Row label="Target">{targetLabel(target, index)}</Row>
        <Row label="Model">
          <span className="font-mono text-code">{target.model}</span>
        </Row>
        <Row label="Parameters">{paramsLabel(target.params)}</Row>
        {target.kind === "messages" ? (
          <Row label="Messages">
            <details className="group">
              <summary className="cursor-pointer text-foreground hover:underline">
                {pluralize(target.messages.length, "message")}, sent unchanged for every item
              </summary>
              <div className="pt-3">
                <TemplateBlocks template={{ kind: "chat", messages: target.messages }} />
              </div>
            </details>
          </Row>
        ) : null}
      </dl>
    </SectionCard>
  );
}

function Row({ label, children }: { label: string; children: ReactNode }) {
  return (
    <>
      <dt className="text-xs font-medium text-muted-foreground sm:pt-0.5">{label}</dt>
      <dd className="min-w-0 [overflow-wrap:anywhere] text-foreground">{children}</dd>
    </>
  );
}
