import { useId, useState, type ReactElement } from "react";
import { toast } from "sonner";

import { DialogHeading } from "@/components/dialog-heading";
import { FormField } from "@/components/form-field";
import { Notice } from "@/components/notice";
import { Button } from "@/components/ui/button";
import { Dialog, DialogClose, DialogContent, DialogTrigger } from "@/components/ui/dialog";
import { Input, Textarea } from "@/components/ui/input";
import type { ScoreConfig, ScoreDataType } from "@/lib/api";

import { ChoiceButtons, type Choice } from "./choice-buttons";
import {
  configFormErrors,
  configFormOf,
  emptyConfigForm,
  toConfigCreate,
  toConfigUpdate,
  type ConfigFormValues,
} from "./score-config-form";
import { isNameTaken, scoreConfigErrorMessage } from "./score-errors";
import { useCreateScoreConfig, useUpdateScoreConfig } from "./scores-queries";

const TYPES: Choice<ScoreDataType>[] = [
  { value: "numeric", label: "numeric" },
  { value: "boolean", label: "boolean" },
  { value: "categorical", label: "categorical" },
];

interface ScoreConfigDialogProps {
  /** The config to edit; without it the dialog creates one. */
  config?: ScoreConfig;
  trigger: ReactElement;
}

/**
 * Figma "Score configs — Create dialog" (and its "name taken" state), also used to edit a config:
 * the name, the type and what the type allows. The name cannot change once created.
 */
export function ScoreConfigDialog({ config, trigger }: ScoreConfigDialogProps) {
  const [open, setOpen] = useState(false);
  const [pending, setPending] = useState(false);
  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (next || !pending) setOpen(next);
      }}
    >
      <DialogTrigger asChild>{trigger}</DialogTrigger>
      {open ? (
        <DialogContent
          hideClose
          className="max-h-[calc(100dvh-2rem)] max-w-[480px] overflow-y-auto rounded-card p-6 sm:p-7"
        >
          <ConfigForm
            config={config}
            onPendingChange={setPending}
            onDone={() => {
              setOpen(false);
            }}
          />
        </DialogContent>
      ) : null}
    </Dialog>
  );
}

interface ConfigFormProps {
  config: ScoreConfig | undefined;
  onPendingChange: (pending: boolean) => void;
  onDone: () => void;
}

function ConfigForm({ config, onPendingChange, onDone }: ConfigFormProps) {
  const typeLabelId = useId();
  const [values, setValues] = useState<ConfigFormValues>(() =>
    config ? configFormOf(config) : emptyConfigForm(),
  );
  const [checked, setChecked] = useState(false);
  const create = useCreateScoreConfig();
  const update = useUpdateScoreConfig();
  const mutation = config ? update : create;
  const errors = checked ? configFormErrors(values, config !== undefined) : {};
  const nameTaken = isNameTaken(mutation.error);
  const name = config?.name ?? values.name.trim();

  function change(patch: Partial<ConfigFormValues>) {
    setValues((current) => ({ ...current, ...patch }));
    if (patch.name !== undefined) {
      mutation.reset();
    }
  }

  function submit() {
    setChecked(true);
    if (Object.keys(configFormErrors(values, config !== undefined)).length > 0) {
      return;
    }
    const options = {
      onSuccess: () => {
        toast.success(config ? `Saved ${name}.` : `Created the ${name} score config.`);
        onDone();
      },
      onSettled: () => {
        onPendingChange(false);
      },
    };
    onPendingChange(true);
    if (config) {
      update.mutate({ id: config.id, update: toConfigUpdate(values, config) }, options);
    } else {
      create.mutate(toConfigCreate(values), options);
    }
  }

  return (
    <form
      noValidate
      className="flex flex-col gap-5"
      onSubmit={(event) => {
        event.preventDefault();
        submit();
      }}
    >
      <DialogHeading
        title={config ? `Edit “${config.name}”` : "New score config"}
        description="Scores sent with this name are checked against it."
        showClose={mutation.isPending ? "disabled" : true}
      />
      <FormField
        label="Name"
        error={nameTaken ? scoreConfigErrorMessage(mutation.error, name) : errors.name}
        hint={
          config
            ? "The name cannot change. Scores refer to it."
            : "Lowercase letters, digits, _ . - (up to 64 characters). Used in the API and the SDK."
        }
      >
        <Input
          value={values.name}
          readOnly={config !== undefined}
          autoComplete="off"
          spellCheck={false}
          maxLength={64}
          placeholder="e.g. helpfulness"
          className="font-mono read-only:bg-surface-muted"
          onChange={(event) => {
            change({ name: event.target.value });
          }}
        />
      </FormField>
      <div className="flex flex-col gap-2">
        <span id={typeLabelId} className="text-label font-semibold text-foreground">
          Type
        </span>
        <ChoiceButtons
          choices={TYPES}
          value={values.dataType}
          a11y={{ labelledBy: typeLabelId }}
          state={{ disabled: mutation.isPending }}
          onChange={(dataType) => {
            change({ dataType });
          }}
        />
      </div>
      <TypeFields values={values} errors={errors} onChange={change} />
      <FormField label="Description (optional)" error={errors.description}>
        <Textarea
          value={values.description}
          rows={2}
          maxLength={500}
          onChange={(event) => {
            change({ description: event.target.value });
          }}
        />
      </FormField>
      {mutation.error && !nameTaken ? (
        <Notice tone="danger" role="alert">
          {scoreConfigErrorMessage(mutation.error, name)}
        </Notice>
      ) : null}
      <div className="flex justify-end gap-2.5">
        <DialogClose asChild>
          <Button disabled={mutation.isPending}>Cancel</Button>
        </DialogClose>
        <Button type="submit" variant="primary" loading={mutation.isPending} disabled={nameTaken}>
          {config ? "Save changes" : "Create config"}
        </Button>
      </div>
    </form>
  );
}

interface TypeFieldsProps {
  values: ConfigFormValues;
  errors: ReturnType<typeof configFormErrors>;
  onChange: (patch: Partial<ConfigFormValues>) => void;
}

/** A numeric config's range, or a categorical config's values; a boolean needs neither. */
function TypeFields({ values, errors, onChange }: TypeFieldsProps) {
  if (values.dataType === "numeric") {
    return (
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        {(["min", "max"] as const).map((bound) => (
          <FormField
            key={bound}
            label={bound === "min" ? "Minimum" : "Maximum"}
            error={errors[bound]}
            hint="Empty means no limit."
          >
            <Input
              value={values[bound]}
              inputMode="decimal"
              autoComplete="off"
              className="font-mono"
              onChange={(event) => {
                onChange({ [bound]: event.target.value });
              }}
            />
          </FormField>
        ))}
      </div>
    );
  }
  if (values.dataType === "categorical") {
    return (
      <FormField
        label="Allowed values"
        error={errors.categories}
        hint="Comma separated. Leave empty to accept any value."
      >
        <Input
          value={values.categories}
          autoComplete="off"
          placeholder="good, ok, bad"
          className="font-mono"
          onChange={(event) => {
            onChange({ categories: event.target.value });
          }}
        />
      </FormField>
    );
  }
  return null;
}
