import { Controller, type UseFormReturn } from "react-hook-form";

import { FormField } from "@/components/form-field";
import { Input } from "@/components/ui/input";
import type { Route } from "@/lib/api";
import { cn } from "@/lib/utils";

import { environmentKind } from "../environment";
import { EnvironmentCombobox } from "./environment-combobox";
import { KEY_NAME_MAX_LENGTH, type KeyFormValues } from "./key-form";
import { KeyListFields } from "./key-list-fields";
import { RouteField } from "./route-field";
import type { DraftField } from "./use-draft-problems";

interface KeyFieldsProps {
  form: UseFormReturn<KeyFormValues>;
  /** The project's routes; undefined while they load. */
  routes: Route[] | undefined;
  routesFailed: boolean;
  onRetryRoutes: () => void;
  /** Reports what each chip field still holds in its text box, so Save can refuse it. */
  reportDraft: (field: DraftField) => (message: string | null) => void;
  /** The create dialog sets related fields side by side; the edit sheet stacks them. */
  layout: "dialog" | "sheet";
  /** The key's cache TTL now, so an API-set TTL that is not a preset still shows. */
  currentCacheTtl: number | null;
}

/** The fields the create dialog and the edit sheet share (Figma "Create key dialog"). */
export function KeyFields({
  form,
  routes,
  routesFailed,
  onRetryRoutes,
  reportDraft,
  layout,
  currentCacheTtl,
}: KeyFieldsProps) {
  const { errors } = form.formState;
  const pair = cn("grid gap-x-3 gap-y-[22px]", layout === "dialog" && "sm:grid-cols-2");

  return (
    <>
      <FormField
        label="Name"
        hint="Name it after the app and where it runs, for example support-bot-prod."
        error={errors.name?.message}
      >
        <Input
          autoComplete="off"
          data-1p-ignore
          data-lpignore="true"
          maxLength={KEY_NAME_MAX_LENGTH}
          placeholder="support-bot-prod"
          {...form.register("name")}
        />
      </FormField>

      <div className={pair}>
        <Controller
          control={form.control}
          name="environment"
          render={({ field }) => (
            <FormField
              label="Environment"
              hint={
                environmentKind(field.value) !== "other"
                  ? "Lab faults never run on production keys."
                  : "Custom environment · shown as other. Lab faults never run on production keys."
              }
              error={errors.environment?.message}
            >
              <EnvironmentCombobox value={field.value} onChange={field.onChange} />
            </FormField>
          )}
        />
        <Controller
          control={form.control}
          name="routeId"
          render={({ field }) => (
            <RouteField
              value={field.value}
              onChange={field.onChange}
              routes={routes}
              routesFailed={routesFailed}
              onRetry={onRetryRoutes}
              error={errors.routeId?.message}
            />
          )}
        />
      </div>

      <div className="grid gap-2">
        <div className={pair}>
          <FormField label="Requests per minute" error={errors.rpmLimit?.message}>
            <Input
              inputMode="numeric"
              autoComplete="off"
              placeholder="No limit"
              {...form.register("rpmLimit")}
            />
          </FormField>
          <FormField label="Tokens per minute" error={errors.tpmLimit?.message}>
            <Input
              inputMode="numeric"
              autoComplete="off"
              placeholder="No limit"
              {...form.register("tpmLimit")}
            />
          </FormField>
        </div>
        <p className="text-xs font-medium text-muted-foreground">
          Leave a limit empty for no limit.
        </p>
      </div>

      <KeyListFields
        form={form}
        reportDraft={reportDraft}
        layout={layout}
        currentCacheTtl={currentCacheTtl}
      />
    </>
  );
}
