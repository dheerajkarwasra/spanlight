import { useState } from "react";
import { toast } from "sonner";

import { DialogHeading } from "@/components/dialog-heading";
import { FieldSelect } from "@/components/field-select";
import { FormField } from "@/components/form-field";
import { Notice } from "@/components/notice";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogFooter } from "@/components/ui/dialog";
import { errorMessage, isApiError, newIdempotencyKey, type Experiment } from "@/lib/api";

import { useStartRun } from "./experiment-queries";
import { versionOptionLabel, type VersionIndex } from "./run-label";
import { usePromptListQuery, usePromptVersionsQuery } from "./use-version-index";

/** Human copy for a refused start. */
function startProblem(error: unknown): string {
  if (isApiError(error)) {
    if (error.code === "RUN_IN_PROGRESS") {
      return "This experiment already has a run in progress. Wait for it to end.";
    }
    if (error.code === "TARGET_OVERRIDE_INVALID") {
      return "Pick a version of the experiment's own prompt.";
    }
    if (error.code === "IDEMPOTENCY_MISMATCH") {
      return "This retry asked for a different run than the first try. Press Start run again.";
    }
    if (error.code === "IDEMPOTENCY_IN_PROGRESS") {
      return "Your first click is still being handled. Wait a moment; the run appears in the list.";
    }
    if (error.status === 404) {
      return "This experiment, or the version you picked, no longer exists.";
    }
  }
  return errorMessage(error);
}

/**
 * Starts a run. One idempotency key per attempt: a retried click after a network failure reuses
 * it, so it never queues a second run. A different version is a different request, so it gets a
 * fresh key (reusing the old one would be refused as a mismatch).
 */
function useStart(experimentId: string, onStarted?: () => void) {
  const start = useStartRun(experimentId);
  const [attempt, setAttempt] = useState(() => ({
    key: newIdempotencyKey(),
    versionId: undefined as string | undefined,
  }));
  function run(versionId?: string) {
    const key = attempt.versionId === versionId ? attempt.key : newIdempotencyKey();
    setAttempt({ key, versionId });
    start.mutate(
      { key, promptVersionId: versionId },
      {
        onSuccess: () => {
          setAttempt({ key: newIdempotencyKey(), versionId });
          toast.success("Run queued. It starts within a few seconds.");
          onStarted?.();
        },
        onError: (error) => {
          // An answer from the server ends this attempt; only a lost response is retried as is.
          if (isApiError(error)) {
            setAttempt({ key: newIdempotencyKey(), versionId });
          }
        },
      },
    );
  }
  return { run, pending: start.isPending, error: start.error };
}

interface StartRunProps {
  experiment: Experiment;
  index: VersionIndex;
  /** A run is queued or running: a second one would be refused. */
  busy: boolean;
}

/** "Start run", and for a prompt target "Run with version…" to compare prompt versions. */
export function StartRun({ experiment, index, busy }: StartRunProps) {
  const { run, pending, error } = useStart(experiment.id);
  const target = experiment.config.target;
  const promptName =
    target.kind === "prompt_version" ? index.get(target.prompt_version_id)?.name : undefined;

  if (busy) {
    return (
      <Button variant="primary" disabled>
        Run in progress
      </Button>
    );
  }
  return (
    <>
      {promptName !== undefined ? (
        <RunWithVersion experimentId={experiment.id} promptName={promptName} />
      ) : null}
      <Button variant="primary" loading={pending} onClick={() => run()}>
        Start run
      </Button>
      {error ? (
        <p role="alert" className="w-full text-right text-xs font-medium text-danger-text">
          {startProblem(error)}
        </p>
      ) : null}
    </>
  );
}

function RunWithVersion({
  experimentId,
  promptName,
}: {
  experimentId: string;
  promptName: string;
}) {
  const [open, setOpen] = useState(false);
  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <Button onClick={() => setOpen(true)}>Run with version…</Button>
      {open ? (
        <DialogContent hideClose className="max-w-[480px]">
          <DialogHeading
            title="Run with another version"
            description={`Runs ${promptName} at the version you pick, with the experiment's credential, model and evaluators, so its runs compare prompt versions.`}
            showClose
          />
          <VersionForm
            experimentId={experimentId}
            promptName={promptName}
            onDone={() => setOpen(false)}
          />
        </DialogContent>
      ) : null}
    </Dialog>
  );
}

function VersionForm({
  experimentId,
  promptName,
  onDone,
}: {
  experimentId: string;
  promptName: string;
  onDone: () => void;
}) {
  const prompts = usePromptListQuery();
  const versions = usePromptVersionsQuery(promptName);
  const [versionId, setVersionId] = useState("");
  const { run, pending, error } = useStart(experimentId, onDone);
  const prompt = prompts.data?.find((item) => item.name === promptName);
  return (
    <form
      className="flex flex-col gap-5"
      onSubmit={(event) => {
        event.preventDefault();
        if (versionId !== "") {
          run(versionId);
        }
      }}
    >
      <FormField label="Version" hint="Each run records the version it used.">
        <FieldSelect
          value={versionId}
          placeholder={versions.isPending ? "Loading versions…" : "Choose a version"}
          onChange={setVersionId}
          options={(versions.data ?? []).map((version) => ({
            value: version.id,
            label: versionOptionLabel(version.version, prompt),
          }))}
        />
      </FormField>
      {error ? (
        <Notice tone="danger" role="alert">
          {startProblem(error)}
        </Notice>
      ) : null}
      <DialogFooter>
        <Button onClick={onDone}>Cancel</Button>
        <Button type="submit" variant="primary" loading={pending} disabled={versionId === ""}>
          Start run
        </Button>
      </DialogFooter>
    </form>
  );
}
