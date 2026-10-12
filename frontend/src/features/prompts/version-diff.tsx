import { Link, useNavigate } from "@tanstack/react-router";
import { Info } from "lucide-react";
import { useState } from "react";

import { ErrorState } from "@/components/error-state";
import { FieldSelect } from "@/components/field-select";
import { FormField } from "@/components/form-field";
import { PageHeader } from "@/components/page-header";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import type { Prompt, PromptVersion } from "@/lib/api";
import { pluralize } from "@/lib/format";

import { BackToPrompt } from "./back-link";
import { CardTitle } from "./card-title";
import {
  configChanges,
  statusCounts,
  templateCounts,
  templateDiff,
  type TemplateDiff,
} from "./diff";
import { ConfigChanges, DiffLines, MessageDiffBlock } from "./diff-lines";
import { LabelDialog } from "./label-dialog";
import { usePromptVersionQuery, usePromptVersionsQuery } from "./prompt-queries";
import { usePromptLink } from "./use-prompt-links";

interface VersionDiffProps {
  prompt: Prompt;
  from: number;
  to: number;
  canWrite: boolean;
}

/** Figma "Prompts — Version diff": pick two versions, see the template and config changes. */
export function VersionDiff({ prompt, from, to, canWrite }: VersionDiffProps) {
  const navigate = useNavigate();
  const [labelVersion, setLabelVersion] = useState<number | null>(null);
  const versions = usePromptVersionsQuery(prompt.name).data?.pages.flatMap((page) => page.items);
  const older = usePromptVersionQuery(prompt.name, from);
  const newer = usePromptVersionQuery(prompt.name, to);
  const open = usePromptLink(prompt.name, { v: to });
  const pick = usePromptLink(prompt.name, { view: "diff" });
  const options = (versions ?? []).map((version) => ({
    value: String(version.version),
    label: `v${String(version.version)} · ${version.commit_message ?? "No commit message"}`,
  }));

  function choose(side: "a" | "b", value: string) {
    const next = { a: from, b: to, [side]: Number(value) };
    void navigate({ ...pick, search: { ...pick.search, ...next }, replace: true });
  }

  return (
    <>
      <BackToPrompt name={prompt.name} version={to} />
      <PageHeader
        title="Version diff"
        description={
          prompt.kind === "chat"
            ? "Message by message. Unchanged messages are collapsed."
            : "Line by line. Red lines were removed, green lines were added."
        }
        actions={
          <>
            <Button asChild>
              <Link {...open}>Open v{to}</Link>
            </Button>
            {canWrite ? (
              <Button variant="primary" onClick={() => setLabelVersion(to)}>
                Set label
              </Button>
            ) : null}
          </>
        }
      />
      <div className="grid gap-3 sm:grid-cols-2 lg:max-w-[692px]">
        <FormField label="From">
          <FieldSelect
            value={String(from)}
            onChange={(value) => choose("a", value)}
            options={options}
          />
        </FormField>
        <FormField label="To">
          <FieldSelect
            value={String(to)}
            onChange={(value) => choose("b", value)}
            options={options}
          />
        </FormField>
      </div>
      <Card
        className="flex flex-col gap-4 p-4 sm:p-6"
        aria-busy={older.isPending || newer.isPending}
      >
        {older.isError || newer.isError ? (
          <ErrorState
            compact
            error={older.error ?? newer.error}
            title="Couldn't load these versions"
            onRetry={() => {
              void older.refetch();
              void newer.refetch();
            }}
          />
        ) : older.data && newer.data ? (
          <DiffBody name={prompt.name} older={older.data} newer={newer.data} />
        ) : (
          <div role="status" aria-label="Loading the diff" className="flex flex-col gap-2">
            <Skeleton className="h-9 w-64" />
            <Skeleton className="h-64 rounded-tile" />
          </div>
        )}
      </Card>
      <LabelDialog
        name={prompt.name}
        labels={prompt.labels}
        versions={versions ?? [prompt.latest]}
        version={labelVersion}
        onClose={() => setLabelVersion(null)}
      />
    </>
  );
}

function diffSubtitle(diff: TemplateDiff, oldLines: number, configChanged: boolean): string {
  const config = configChanged ? " · config changed" : "";
  if (diff.kind === "chat") {
    const counts = statusCounts(diff.messages);
    const parts = [
      counts.edited > 0 ? `${pluralize(counts.edited, "message")} edited` : null,
      counts.unchanged > 0 ? `${String(counts.unchanged)} unchanged` : null,
      counts.added > 0 ? `${String(counts.added)} added` : null,
      counts.removed > 0 ? `${String(counts.removed)} removed` : null,
    ].filter((part) => part !== null);
    return `Chat · ${parts.join(" · ")}${config}`;
  }
  if (diff.kind === "mixed") {
    return `The kind changed${config}`;
  }
  const newLines = diff.lines.filter((line) => line.newNumber !== null).length;
  return `Template · text · ${String(oldLines)} → ${String(newLines)} lines${config}`;
}

function DiffBody({
  name,
  older,
  newer,
}: {
  name: string;
  older: PromptVersion;
  newer: PromptVersion;
}) {
  const diff = templateDiff(older.template, newer.template);
  const changes = configChanges(older.model_config, newer.model_config);
  const counts = templateCounts(diff);
  const oldLines = diff.kind === "text" ? diff.oldLineCount : 0;
  return (
    <>
      <CardTitle
        title={`${name} · v${String(older.version)} → v${String(newer.version)}`}
        subtitle={diffSubtitle(diff, oldLines, changes.length > 0)}
        actions={
          <span className="flex gap-2">
            <Badge variant="success">+{counts.added} added</Badge>
            <Badge variant="danger">−{counts.removed} removed</Badge>
          </span>
        }
      />
      {diff.kind === "text" ? <DiffLines lines={diff.lines} label="Template lines" /> : null}
      {diff.kind === "chat"
        ? diff.messages.map((message) => <MessageDiffBlock key={message.index} message={message} />)
        : null}
      {changes.length > 0 ? (
        <ConfigChanges changes={changes} />
      ) : (
        <p className="flex items-center gap-2 px-1 text-xs font-medium text-muted-foreground">
          <Info aria-hidden className="size-3.5 shrink-0" />
          Model config is unchanged between these versions.
        </p>
      )}
    </>
  );
}
