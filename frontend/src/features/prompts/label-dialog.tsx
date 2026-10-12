import { CircleAlert } from "lucide-react";
import { useId, useState } from "react";
import { toast } from "sonner";

import { Callout } from "@/components/callout";
import { DialogHeading } from "@/components/dialog-heading";
import { FieldSelect } from "@/components/field-select";
import { FormField } from "@/components/form-field";
import { Button } from "@/components/ui/button";
import { Dialog, DialogClose, DialogContent } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import type { PromptLabel, PromptVersion } from "@/lib/api";
import { useReturnFocus } from "@/lib/use-return-focus";

import { labelProblem } from "./draft";
import { LATEST_RESERVED_REST, promptErrorMessage } from "./prompt-errors";
import { useSetLabel } from "./prompt-queries";

const PATTERN_HINT = "Lowercase letters, digits, - and _, up to 32 characters.";

interface LabelDialogProps {
  name: string;
  labels: readonly PromptLabel[];
  versions: readonly PromptVersion[];
  /** The version the dialog opens on; null while closed. */
  version: number | null;
  onClose: () => void;
}

/** Figma "Prompts — Label dialog": point a label at a version. `latest` is refused in place. */
export function LabelDialog({ name, labels, versions, version, onClose }: LabelDialogProps) {
  const setLabel = useSetLabel(name);
  const returnFocus = useReturnFocus();
  return (
    <Dialog
      open={version !== null}
      onOpenChange={(open) => {
        if (!open && !setLabel.isPending) {
          onClose();
        }
      }}
    >
      {version === null ? null : (
        <LabelForm
          name={name}
          labels={labels}
          versions={versions}
          initialVersion={version}
          setLabel={setLabel}
          returnFocus={returnFocus}
          onDone={onClose}
        />
      )}
    </Dialog>
  );
}

interface LabelFormProps {
  name: string;
  labels: readonly PromptLabel[];
  versions: readonly PromptVersion[];
  initialVersion: number;
  setLabel: ReturnType<typeof useSetLabel>;
  returnFocus: ReturnType<typeof useReturnFocus>;
  onDone: () => void;
}

function LabelForm(props: LabelFormProps) {
  const { name, labels, versions, initialVersion, setLabel, onDone } = props;
  const [label, setLabelText] = useState("production");
  const [version, setVersion] = useState(String(initialVersion));
  const [failure, setFailure] = useState<string | null>(null);
  const problem = labelProblem(label);

  async function submit() {
    if (problem !== null) {
      return;
    }
    setFailure(null);
    try {
      const saved = await setLabel.mutateAsync({ label, version: Number(version) });
      const moved = labels.some((existing) => existing.label === saved.label);
      toast.success(`${moved ? "Moved" : "Set"} ${saved.label} to v${String(saved.version)}.`);
      onDone();
    } catch (error) {
      setFailure(promptErrorMessage(error));
    }
  }

  return (
    <DialogContent
      hideClose
      className="max-w-[480px] gap-5 rounded-card p-6 sm:p-7"
      {...props.returnFocus}
    >
      <DialogHeading
        title="Set label"
        description={`Point a label at a version of ${name}. Moving a label does not change any version.`}
        showClose={setLabel.isPending ? "disabled" : true}
      />
      <form
        noValidate
        className="flex flex-col gap-5"
        onSubmit={(event) => {
          event.preventDefault();
          void submit();
        }}
      >
        <LabelField value={label} onChange={setLabelText} problem={problem} labels={labels} />
        <FormField label="Version">
          <FieldSelect
            value={version}
            onChange={setVersion}
            options={versions.map((option) => ({
              value: String(option.version),
              label: `v${String(option.version)} · ${option.commit_message ?? "No commit message"}`,
            }))}
          />
        </FormField>
        {failure === null ? null : (
          <Callout tone="danger" role="alert">
            {failure}
          </Callout>
        )}
        <div className="flex justify-end gap-2.5">
          <DialogClose asChild>
            <Button disabled={setLabel.isPending}>Cancel</Button>
          </DialogClose>
          <Button
            type="submit"
            variant="primary"
            disabled={problem !== null}
            loading={setLabel.isPending}
          >
            Save label
          </Button>
        </div>
      </form>
    </DialogContent>
  );
}

interface LabelFieldProps {
  value: string;
  onChange: (value: string) => void;
  problem: ReturnType<typeof labelProblem>;
  labels: readonly PromptLabel[];
}

function LabelField({ value, onChange, problem, labels }: LabelFieldProps) {
  const id = useId();
  const showError = problem === "reserved" || problem === "pattern";
  const existing = labels.map((label) => `${label.label} → v${String(label.version)}`).join(", ");
  return (
    <div className="flex flex-col gap-2">
      <Label htmlFor={id}>Label</Label>
      <Input
        id={id}
        value={value}
        autoComplete="off"
        spellCheck={false}
        aria-invalid={showError || undefined}
        aria-describedby={`${id}-help`}
        onChange={(event) => onChange(event.target.value)}
      />
      <p
        id={`${id}-help`}
        className={
          showError
            ? "flex items-start gap-1.5 text-xs font-medium text-danger-text"
            : "text-xs font-medium text-muted-foreground"
        }
      >
        {showError ? (
          <CircleAlert aria-hidden className="mt-px size-3.5 shrink-0" strokeWidth={2.25} />
        ) : null}
        {problem === "reserved" ? (
          <span>
            <code className="font-mono">latest</code>
            {LATEST_RESERVED_REST}
          </span>
        ) : problem === "pattern" ? (
          PATTERN_HINT
        ) : existing === "" ? (
          PATTERN_HINT
        ) : (
          `Existing labels: ${existing}.`
        )}
      </p>
    </div>
  );
}
