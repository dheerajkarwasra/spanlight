import { Link } from "@tanstack/react-router";
import { CircleCheck, Copy, FlaskConical, X } from "lucide-react";
import { useState } from "react";

import { SegmentedControl } from "@/components/ui/segmented-control";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Tooltip } from "@/components/ui/tooltip";
import { usePermission, useProjectParams } from "@/features/shell";
import { toPathPromptName, type Credential, type Price } from "@/lib/api";

import { ConfigForm } from "./config-form";
import { runHeadingId, type ColumnState, type SourceKind } from "./config-state";
import { MessagesSourceFields } from "./messages-source-fields";
import { PromptSourceFields } from "./prompt-source-fields";
import { ResultCard } from "./result-card";
import { SaveVersionDialog } from "./save-version-dialog";
import type { SavedVersion } from "./save-version-form";
import type { RunState } from "./run-state";

const SOURCE_OPTIONS = [
  { value: "prompt", label: "Prompt version" },
  { value: "messages", label: "Messages" },
] as const;

interface ConfigColumnProps {
  column: ColumnState;
  letter: string;
  state: RunState;
  credentials: readonly Credential[];
  prices: readonly Price[];
  canAdd: boolean;
  canRemove: boolean;
  onChange: (patch: Partial<ColumnState>) => void;
  onRemove: () => void;
  onDuplicate: () => void;
}

function SavedNote({ saved, canRun }: { saved: SavedVersion; canRun: boolean }) {
  const { orgId, projectId } = useProjectParams();
  return (
    <p
      role="status"
      className="flex flex-wrap items-center gap-2 rounded-input bg-success-subtle px-3 py-2.5 text-label font-medium text-success"
    >
      <CircleCheck aria-hidden className="size-4 shrink-0" />
      Saved as v{saved.version}.
      <Link
        to="/$orgId/$projectId/prompts/$name"
        params={{ orgId, projectId, name: toPathPromptName(saved.name) }}
        className="rounded-sm font-semibold text-accent hover:underline"
      >
        Open version
      </Link>
      {canRun ? (
        <Link
          to="/$orgId/$projectId/experiments/new"
          params={{ orgId, projectId }}
          search={{ prompt: saved.name, version: saved.id }}
          className="rounded-sm font-semibold text-accent hover:underline"
        >
          Create experiment
        </Link>
      ) : null}
    </p>
  );
}

/** One playground column (Figma "Run A"): header, source, credential, model, params, output. */
export function ConfigColumn(props: ConfigColumnProps) {
  const { column, letter, state, onChange } = props;
  const { orgId, projectId } = useProjectParams();
  const canRun = usePermission("experiments:run");
  const [saved, setSaved] = useState<SavedVersion | null>(null);
  const messages = column.sourceKind === "messages";

  return (
    <Card
      role="group"
      aria-label={`Run ${letter}`}
      className="flex min-w-0 flex-1 flex-col gap-3.5 rounded-card p-5"
    >
      <div className="flex items-center gap-2">
        <span
          aria-hidden
          className="flex size-6 items-center justify-center rounded-full bg-ink text-xs font-bold text-ink-foreground"
        >
          {letter}
        </span>
        <h2
          id={runHeadingId(column.key)}
          tabIndex={-1}
          className="min-w-0 flex-1 text-[15px] font-bold text-foreground outline-none"
        >
          Run {letter}
        </h2>
        {messages ? (
          <SaveVersionDialog
            column={column}
            letter={letter}
            onSaved={setSaved}
            trigger={
              <Button variant="secondary" size="sm">
                Save as version
              </Button>
            }
          />
        ) : null}
        {canRun && !messages && column.version ? (
          <Tooltip content="Create experiment">
            <Button size="icon-sm" asChild>
              <Link
                to="/$orgId/$projectId/experiments/new"
                params={{ orgId, projectId }}
                search={{ prompt: column.version.promptName, version: column.version.id }}
                aria-label={`Create experiment from run ${letter}`}
              >
                <FlaskConical aria-hidden />
              </Link>
            </Button>
          </Tooltip>
        ) : null}
        <Button
          variant="ghost"
          size="icon-sm"
          aria-label={`Duplicate run ${letter}`}
          disabled={!props.canAdd}
          onClick={props.onDuplicate}
        >
          <Copy aria-hidden />
        </Button>
        {props.canRemove ? (
          <Button
            variant="ghost"
            size="icon-sm"
            aria-label={`Remove run ${letter}`}
            onClick={props.onRemove}
          >
            <X aria-hidden />
          </Button>
        ) : null}
      </div>
      <SegmentedControl<SourceKind>
        aria-label={`Source of run ${letter}`}
        tone="surface"
        value={column.sourceKind}
        onValueChange={(sourceKind) => onChange({ sourceKind })}
        options={SOURCE_OPTIONS}
        className="self-start"
      />
      {messages ? (
        <MessagesSourceFields column={column} onChange={onChange} />
      ) : (
        <PromptSourceFields column={column} onChange={onChange} />
      )}
      <ConfigForm
        column={column}
        onChange={onChange}
        credentials={props.credentials}
        prices={props.prices}
      />
      {saved ? <SavedNote saved={saved} canRun={canRun} /> : null}
      <ResultCard state={state} />
    </Card>
  );
}
