import type { ReactNode } from "react";

import { Card } from "@/components/ui/card";
import type { ModelAdvice, PromptTemplate, PromptVersion } from "@/lib/api";

import { CardTitle } from "./card-title";
import { HighlightedText } from "./highlighted-text";

/** "claude-haiku-5-5 · temperature 0.2 · max tokens 512", or a note that there is no advice. */
export function AdviceSummary({ advice }: { advice: ModelAdvice }) {
  const parts: string[] = [];
  if (advice.temperature != null) {
    parts.push(`temperature ${String(advice.temperature)}`);
  }
  if (advice.max_tokens != null) {
    parts.push(`max tokens ${String(advice.max_tokens)}`);
  }
  if (advice.top_p != null) {
    parts.push(`top p ${String(advice.top_p)}`);
  }
  if (!advice.model && parts.length === 0) {
    return "No model config";
  }
  return (
    <>
      {advice.model ? <span className="font-mono">{advice.model}</span> : null}
      {advice.model && parts.length > 0 ? " · " : null}
      {parts.join(" · ")}
    </>
  );
}

/** Figma "Template · v3": the version's template, read-only, variables in violet. */
export function TemplateCard({ version }: { version: PromptVersion }) {
  return (
    <Card className="flex flex-col gap-4 p-5 sm:p-6">
      <CardTitle
        title={`Template · v${String(version.version)}`}
        subtitle={<AdviceSummary advice={version.model_config} />}
      />
      <TemplateBlocks template={version.template} />
    </Card>
  );
}

export function TemplateBlocks({ template }: { template: PromptTemplate }) {
  if (template.kind === "text") {
    return (
      <TemplateBlock label={null}>
        <HighlightedText text={template.text} />
      </TemplateBlock>
    );
  }
  return (
    <ol className="flex flex-col gap-3" aria-label="Messages">
      {template.messages.map((message, index) => (
        <li key={index}>
          <TemplateBlock label={message.role}>
            <HighlightedText text={message.content} />
          </TemplateBlock>
        </li>
      ))}
    </ol>
  );
}

function TemplateBlock({ label, children }: { label: string | null; children: ReactNode }) {
  return (
    <div className="flex flex-col gap-1.5 rounded-input bg-surface-muted p-3.5">
      {label ? (
        <p className="text-overline font-semibold text-subtle-foreground uppercase">{label}</p>
      ) : null}
      <p className="font-mono text-[13px] leading-5 [overflow-wrap:anywhere] whitespace-pre-wrap text-foreground">
        {children}
      </p>
    </div>
  );
}
