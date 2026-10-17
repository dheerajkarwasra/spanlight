import { useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { toast } from "sonner";

import { useProjectParams } from "@/features/shell";
import { errorMessage, isApiError, queryKeys, type BillingSource } from "@/lib/api";

import { useCreateSource, useUpdateSource } from "./billing-sources-queries";
import {
  emptySourceForm,
  isNotConfigured,
  notConfiguredCopy,
  sourceErrorsOf,
  sourceFormOf,
  toSourceCreate,
  toSourceUpdate,
  validateSourceForm,
  type FieldErrors,
  type SourceFormValues,
} from "./source-form";

interface SourceFormOptions {
  /** The source being edited; absent when adding one. */
  source: BillingSource | undefined;
  /** The server has no CREDENTIALS_KEYS, so a new source starts as a CSV source. */
  apiKindsOff: boolean;
  onDone: () => void;
  /** The server answered `409 NOT_CONFIGURED`; `copy` is the notice to show (its detail). */
  onNotConfigured: (copy: string) => void;
}

function announceCreated(created: BillingSource) {
  if (created.status === "failing") {
    toast.warning(
      `Added "${created.name}", but the key check failed: ${created.last_error ?? "unknown error"}. Rotate the key to fix it.`,
    );
  } else {
    toast.success(`Added billing source "${created.name}".`);
  }
}

/**
 * The create and edit dialog's state and submit. The typed admin key lives in this state only:
 * it is sent through `useUncachedAction` and cleared once sent, whatever the answer.
 */
export function useSourceForm({ source, apiKindsOff, onDone, onNotConfigured }: SourceFormOptions) {
  const { orgId } = useProjectParams();
  const queryClient = useQueryClient();
  const createSource = useCreateSource();
  const updateSource = useUpdateSource();
  const [values, setValues] = useState<SourceFormValues>(() => {
    if (source) {
      return sourceFormOf(source);
    }
    const empty = emptySourceForm();
    return apiKindsOff ? { ...empty, kind: "csv_upload" } : empty;
  });
  const [errors, setErrors] = useState<FieldErrors>({});

  function update(patch: Partial<SourceFormValues>) {
    setValues((current) => ({ ...current, ...patch }));
    // A field's error goes once the field changes; the others stay until the next submit.
    setErrors((current) =>
      Object.fromEntries(Object.entries(current).filter(([field]) => !(field in patch))),
    );
  }

  function refused(error: unknown) {
    const fieldErrors = sourceErrorsOf(error);
    if (isApiError(error) && error.code === "BILLING_SOURCE_PROJECT_TAKEN") {
      // Another source took a project meanwhile: reload the list so the picker shows it.
      void queryClient.invalidateQueries({ queryKey: queryKeys.org(orgId).billingSources });
    }
    if (fieldErrors) {
      setErrors(fieldErrors);
    } else {
      toast.error(errorMessage(error));
    }
  }

  async function create() {
    const result = await createSource.run(toSourceCreate(values));
    setValues((current) => ({ ...current, adminKey: "" }));
    if (result.ok) {
      announceCreated(result.value);
      onDone();
    } else if (isNotConfigured(result.error)) {
      onNotConfigured(notConfiguredCopy(result.error));
      setValues((current) => ({ ...current, kind: "csv_upload" }));
    } else {
      refused(result.error);
    }
  }

  function save(stored: BillingSource) {
    updateSource.mutate(
      { sourceId: stored.id, input: toSourceUpdate(values, stored) },
      {
        onSuccess: (saved) => {
          toast.success(`Saved billing source "${saved.name}".`);
          onDone();
        },
        onError: refused,
      },
    );
  }

  function submit() {
    const found = validateSourceForm(values, source ? "edit" : "create");
    setErrors(found);
    if (Object.keys(found).length > 0) {
      return;
    }
    if (source) {
      save(source);
    } else {
      void create();
    }
  }

  return {
    values,
    errors,
    update,
    submit,
    pending: createSource.pending || updateSource.isPending,
  };
}
