import { useWatch, type UseFormReturn } from "react-hook-form";

import { CodeBlock } from "@/components/code-block";
import { SectionCard } from "@/components/section-card";

import { RULE_HINTS } from "./evaluator-labels";
import { useAlertChannelsQuery } from "./evaluator-queries";
import { EXPECTED_OUTPUT_RULES, type EvaluatorFormValues } from "./evaluator-schema";
import { JudgeSideCard } from "./judge-side-card";

function webhookPreview(name: string, channel: string): string {
  return [
    `POST ${channel}`,
    `{ "version": "2026-10-01",`,
    `  "trace_id": "…",`,
    `  "input": …, "output": …,`,
    `  "truncated": false }`,
    ``,
    `200 {"scores": [{"name": "${name || "name"}", "value": 1}]}`,
  ].join("\n");
}

/**
 * The right-hand card of the form: how a rule scores, where an expected-output rule runs, the
 * judge's prompt, or the webhook request's shape. The channel's URL is a secret and never shown.
 */
export function FormSideCard({ form }: { form: UseFormReturn<EvaluatorFormValues> }) {
  const [kind, rule, name, channelId] = useWatch({
    control: form.control,
    name: ["kind", "rule", "name", "channelId"],
  });
  const channels = useAlertChannelsQuery().data;

  if (kind === "llm_judge") {
    return <JudgeSideCard form={form} />;
  }
  if (kind === "webhook") {
    const channel = channels?.find((candidate) => candidate.id === channelId)?.name;
    return (
      <SectionCard
        title="Request preview"
        description="Signed with the channel secret, payload version 2026-10-01."
        className="gap-4"
      >
        <CodeBlock
          label="Webhook request and reply"
          code={webhookPreview(name.trim(), channel ? `to ${channel}` : "to your channel")}
        />
      </SectionCard>
    );
  }
  if (EXPECTED_OUTPUT_RULES.has(rule)) {
    return (
      <SectionCard title="Where it runs" className="gap-3">
        <p className="text-sm text-muted-foreground">
          Attach this evaluator to an experiment and every dataset item is scored against its
          expected output.
        </p>
        <p className="text-sm text-muted-foreground">
          Rules that compare with an expected output are exact match and Levenshtein.
        </p>
      </SectionCard>
    );
  }
  return (
    <SectionCard title="How this scores" className="gap-3">
      <p className="text-sm text-muted-foreground">{RULE_HINTS[rule]}</p>
      <p className="text-sm text-muted-foreground">
        It is stored as a {rule === "length" ? "numeric" : "boolean"} score and shows on the trace
        as Eval · {name.trim() || "name"}.
      </p>
    </SectionCard>
  );
}
