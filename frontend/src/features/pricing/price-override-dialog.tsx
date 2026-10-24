import { zodResolver } from "@hookform/resolvers/zod";
import type { ComponentProps } from "react";
import { useForm, useWatch } from "react-hook-form";
import { toast } from "sonner";

import { DialogHeading } from "@/components/dialog-heading";
import { FormField } from "@/components/form-field";
import { ReadOnlyNote } from "@/components/read-only-note";
import { Button } from "@/components/ui/button";
import { Dialog, DialogClose, DialogContent, DialogFooter } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { todayUtc } from "@/features/billing-sources/window-picker";
import { usePermission } from "@/features/shell";
import { errorMessage, isApiError } from "@/lib/api";
import { applyServerFieldErrors } from "@/lib/form-errors";
import { viewOnlyCopy } from "@/lib/permissions";

import {
  caseHint,
  emptyOverrideValues,
  overrideFormSchema,
  PATTERN_MAX_LENGTH,
  PROVIDER_MAX_LENGTH,
  toOverrideInput,
  type OverrideFormValues,
  type OverridePrefill,
} from "./price-override-form";
import { useCreatePriceOverride } from "./pricing-queries";

const TITLE = "Add price override";
const DESCRIPTION =
  "Replaces the book price for new spans in every project of this organization. Stored costs are not recomputed.";
const FIELDS = Object.keys(overrideFormSchema.shape) as (keyof OverrideFormValues)[];

const RATES = [
  ["input_per_mtok", "Input per 1M"],
  ["output_per_mtok", "Output per 1M"],
  ["cached_input_per_mtok", "Cached input per 1M (optional)"],
  ["cache_write_5m_per_mtok", "Cache write 5 m per 1M (optional)"],
  ["cache_write_1h_per_mtok", "Cache write 1 h per 1M (optional)"],
] as const;

interface PriceOverrideDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** Provider and pattern from an unpriced model or a link (spec ruling 8). */
  initial?: OverridePrefill | undefined;
}

/**
 * Figma "Pricing — Add price override" (714:1206). Without `gateway:write` it explains who can
 * add one instead of showing the form. Mounted only while open, so each opening starts fresh.
 */
export function PriceOverrideDialog({ open, onOpenChange, initial }: PriceOverrideDialogProps) {
  const canWrite = usePermission("gateway:write");
  const create = useCreatePriceOverride();
  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        // Escape, Cancel and an outside click may not close it mid-save.
        if (next || !create.isPending) {
          onOpenChange(next);
        }
      }}
    >
      {open ? (
        <DialogContent
          hideClose
          className="max-h-[calc(100dvh-2rem)] max-w-[560px] gap-0 overflow-y-auto rounded-card p-6 sm:p-7"
        >
          {canWrite ? (
            <OverrideForm initial={initial} create={create} onSaved={() => onOpenChange(false)} />
          ) : (
            <div className="grid gap-[22px]">
              <DialogHeading title={TITLE} description={DESCRIPTION} />
              <ReadOnlyNote>{viewOnlyCopy("price overrides", "gateway:write")}</ReadOnlyNote>
            </div>
          )}
        </DialogContent>
      ) : null}
    </Dialog>
  );
}

interface OverrideFormProps {
  initial: OverridePrefill | undefined;
  create: ReturnType<typeof useCreatePriceOverride>;
  onSaved: () => void;
}

function OverrideForm({ initial, create, onSaved }: OverrideFormProps) {
  const form = useForm<OverrideFormValues>({
    resolver: zodResolver(overrideFormSchema),
    defaultValues: emptyOverrideValues(initial, todayUtc()),
  });
  const errors = form.formState.errors;
  const patternHint = caseHint(useWatch({ control: form.control, name: "model_pattern" }));

  const onSubmit = form.handleSubmit(async (values) => {
    try {
      const created = await create.mutateAsync(toOverrideInput(values));
      toast.success(`Added a price for ${created.provider} ${created.model_pattern}.`);
      onSaved();
    } catch (error) {
      if (isApiError(error) && error.code === "PRICE_OVERRIDE_EXISTS") {
        form.setError("model_pattern", {
          type: "server",
          message: "An override for this provider and pattern already exists.",
        });
      } else if (!applyServerFieldErrors(error, form.setError, FIELDS)) {
        toast.error(errorMessage(error));
      }
    }
  });

  return (
    <form onSubmit={onSubmit} noValidate className="grid gap-[22px]">
      <DialogHeading
        title={TITLE}
        description={DESCRIPTION}
        showClose={create.isPending ? "disabled" : true}
      />
      <div className="grid gap-[22px] sm:grid-cols-2 sm:gap-4">
        <FormField label="Provider" error={errors.provider?.message}>
          <Input
            autoComplete="off"
            maxLength={PROVIDER_MAX_LENGTH}
            placeholder="openai"
            {...form.register("provider")}
          />
        </FormField>
        <FormField
          label="Model pattern"
          hint={patternHint ?? "Exact model or with a snapshot suffix."}
          error={errors.model_pattern?.message}
        >
          <Input
            autoComplete="off"
            maxLength={PATTERN_MAX_LENGTH}
            className="font-mono"
            {...form.register("model_pattern")}
          />
        </FormField>
        {RATES.slice(0, 3).map(([name, label]) => (
          <FormField key={name} label={label} error={errors[name]?.message}>
            <RateInput {...form.register(name)} />
          </FormField>
        ))}
        <FormField label="Effective from" hint="In UTC." error={errors.effective_from?.message}>
          <Input type="date" className="tabular" {...form.register("effective_from")} />
        </FormField>
        {RATES.slice(3).map(([name, label]) => (
          <FormField key={name} label={label} error={errors[name]?.message}>
            <RateInput {...form.register(name)} />
          </FormField>
        ))}
      </div>
      <DialogFooter className="flex-row justify-end gap-2.5 max-sm:[&>*]:flex-1">
        <DialogClose asChild>
          <Button disabled={create.isPending}>Cancel</Button>
        </DialogClose>
        <Button type="submit" variant="primary" loading={create.isPending}>
          Add override
        </Button>
      </DialogFooter>
    </form>
  );
}

/** A USD rate with its "$" prefix, as the budget amount field shows it. */
function RateInput(props: ComponentProps<"input">) {
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
