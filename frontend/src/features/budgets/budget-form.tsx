import { zodResolver } from "@hookform/resolvers/zod";
import { useEffect, type ComponentProps } from "react";
import { Controller, useForm, useWatch } from "react-hook-form";

import { Callout } from "@/components/callout";
import { DialogHeading } from "@/components/dialog-heading";
import { FormField } from "@/components/form-field";
import { Button } from "@/components/ui/button";
import { DialogClose, DialogFooter } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { SegmentedControl } from "@/components/ui/segmented-control";
import { useProjectQuery } from "@/features/shell";
import type { Budget } from "@/lib/api";

import { BudgetChannelsField } from "./budget-channels-field";
import { PERIOD_LABELS } from "./budget-period";
import {
  ACTION_HINTS,
  BUDGET_NAME_MAX_LENGTH,
  EMPTY_BUDGET_VALUES,
  budgetFormSchema,
  valuesFromBudget,
  type BudgetFormValues,
} from "./budget-schema";
import { ScopePicker } from "./scope-picker";
import { useBudgetSubmit } from "./use-budget-submit";

const PERIOD_OPTIONS = (["daily", "monthly"] as const).map((value) => ({
  value,
  label: PERIOD_LABELS[value],
}));

const ACTION_OPTIONS = [
  { value: "notify", label: "Notify" },
  { value: "block", label: "Block" },
] as const;

interface BudgetFormProps {
  /** Null to create a budget. */
  budget: Budget | null;
  onPendingChange: (pending: boolean) => void;
  onSaved: () => void;
}

/** Figma "Budgets — Budget dialog": name, scope, period and amount, action, channels. */
export function BudgetForm({ budget, onPendingChange, onSaved }: BudgetFormProps) {
  const projectName = useProjectQuery().data?.name ?? "this project";
  const form = useForm<BudgetFormValues>({
    resolver: zodResolver(budgetFormSchema),
    defaultValues: budget ? valuesFromBudget(budget) : EMPTY_BUDGET_VALUES,
  });
  const action = useWatch({ control: form.control, name: "action" });
  const period = useWatch({ control: form.control, name: "period" });
  const submit = useBudgetSubmit(form, budget, projectName, onSaved);
  const errors = form.formState.errors;

  useEffect(() => {
    onPendingChange(submit.pending);
    return () => {
      onPendingChange(false);
    };
  }, [submit.pending, onPendingChange]);

  return (
    <form onSubmit={submit.onSubmit} noValidate className="grid gap-[22px]">
      <DialogHeading
        title={budget ? `Edit ${budget.name}` : "Create budget"}
        description="Set a spend limit and choose what happens when it is passed."
        showClose={submit.pending ? "disabled" : true}
      />
      {submit.problem ? (
        <Callout tone="danger" role="alert">
          {submit.problem}
        </Callout>
      ) : null}
      <FormField
        label="Name"
        hint="Shown in the budget list and in alerts."
        error={errors.name?.message}
      >
        <Input
          autoComplete="off"
          autoFocus
          maxLength={BUDGET_NAME_MAX_LENGTH}
          placeholder="Monthly project cap"
          {...form.register("name")}
        />
      </FormField>
      <ScopePicker form={form} projectName={projectName} />
      <div className="grid gap-[22px] sm:grid-cols-2 sm:gap-4">
        <div className="flex flex-col gap-2">
          <p className="text-sm font-semibold text-foreground">Period</p>
          <SegmentedControl
            aria-label="Period"
            tone="surface"
            value={period}
            options={PERIOD_OPTIONS}
            onValueChange={(next) => form.setValue("period", next)}
            className="grid h-[46px] w-full grid-cols-2"
          />
        </div>
        <FormField label="Amount (USD)" error={errors.amount?.message}>
          <AmountInput {...form.register("amount")} />
        </FormField>
      </div>
      <div className="flex flex-col gap-2">
        <p className="text-sm font-semibold text-foreground">Action</p>
        <SegmentedControl
          aria-label="Action"
          tone="surface"
          value={action}
          options={ACTION_OPTIONS}
          onValueChange={(next) => form.setValue("action", next)}
          className="grid w-full grid-cols-2"
        />
        <p className="text-xs font-medium text-muted-foreground">{ACTION_HINTS[action]}</p>
      </div>
      <Controller
        control={form.control}
        name="channelIds"
        render={({ field, fieldState }) => (
          <BudgetChannelsField
            value={field.value}
            onChange={field.onChange}
            error={fieldState.error?.message}
          />
        )}
      />
      <DialogFooter className="flex-row justify-end gap-2.5 max-sm:[&>*]:flex-1">
        <DialogClose asChild>
          <Button disabled={submit.pending}>Cancel</Button>
        </DialogClose>
        <Button type="submit" variant="primary" loading={submit.pending}>
          {budget ? "Save changes" : "Create budget"}
        </Button>
      </DialogFooter>
    </form>
  );
}

/** The amount field with its "$" prefix (Figma "Amount (USD)"). */
function AmountInput(props: ComponentProps<"input">) {
  return (
    <div className="relative">
      <span
        aria-hidden
        className="pointer-events-none absolute top-1/2 left-4 -translate-y-1/2 text-sm text-muted-foreground"
      >
        $
      </span>
      <Input
        inputMode="decimal"
        autoComplete="off"
        placeholder="0.00"
        className="pl-8 tabular"
        {...props}
      />
    </div>
  );
}
