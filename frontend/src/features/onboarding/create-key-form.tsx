import { zodResolver } from "@hookform/resolvers/zod";
import { useQueryClient } from "@tanstack/react-query";
import { CircleAlert, KeyRound } from "lucide-react";
import { useId, useState } from "react";
import { useForm } from "react-hook-form";
import { toast } from "sonner";
import { z } from "zod";

import { Notice } from "@/components/notice";
import { Button } from "@/components/ui/button";
import { errorMessage, projectsApi, queryKeys, type CreatedApiKey } from "@/lib/api";
import { applyServerFieldErrors } from "@/lib/form-errors";
import { useUncachedAction } from "@/lib/use-uncached-action";
import { cn } from "@/lib/utils";

import { INK_FOCUS, KEY_FIELD_CLASSES } from "./api-key-styles";

const KEY_NAME_MAX_LENGTH = 100;

const keySchema = z.object({
  name: z
    .string()
    .trim()
    .min(1, "Enter a key name.")
    .max(KEY_NAME_MAX_LENGTH, `Use ${KEY_NAME_MAX_LENGTH} characters or fewer.`),
});

type KeyValues = z.infer<typeof keySchema>;

/** Names and creates the project's first key, on the ink card. */
export function CreateKeyForm({
  projectId,
  onCreated,
}: {
  projectId: string;
  onCreated: (key: CreatedApiKey) => void;
}) {
  const id = useId();
  const inputId = `${id}-name`;
  const hintId = `${id}-hint`;
  const errorId = `${id}-error`;
  const queryClient = useQueryClient();
  const [formError, setFormError] = useState<string | null>(null);
  const form = useForm<KeyValues>({
    resolver: zodResolver(keySchema),
    defaultValues: { name: "Onboarding key" },
  });

  // The answer carries the key's secret once, so it stays out of the query client's cache.
  const createKey = useUncachedAction((name: string) => projectsApi.createKey(projectId, { name }));

  const onSubmit = form.handleSubmit(async ({ name }) => {
    setFormError(null);
    const result = await createKey.run(name);
    if (result.ok) {
      void queryClient.invalidateQueries({ queryKey: queryKeys.project(projectId).keys });
      toast.success("API key created");
      onCreated(result.value);
    } else if (!applyServerFieldErrors(result.error, form.setError, ["name"])) {
      setFormError(errorMessage(result.error));
    }
  });

  const fieldError = form.formState.errors.name?.message;

  return (
    <form
      noValidate
      className="flex flex-col gap-2"
      onSubmit={(event) => {
        void onSubmit(event);
      }}
    >
      <label htmlFor={inputId} className="text-label font-semibold text-rail-foreground">
        Key name
      </label>
      {/* The shared Input is drawn for light surfaces; here the pill is the field and owns the ring. */}
      <div
        className={cn(
          KEY_FIELD_CLASSES,
          "has-[input:focus-visible]:outline-[1.5px] has-[input:focus-visible]:outline-offset-2 has-[input:focus-visible]:outline-lime",
          fieldError && "ring-[1.5px] ring-rail-danger",
        )}
      >
        <input
          id={inputId}
          autoComplete="off"
          maxLength={KEY_NAME_MAX_LENGTH}
          aria-invalid={fieldError ? true : undefined}
          aria-describedby={fieldError ? errorId : hintId}
          className="h-10 min-w-0 flex-1 bg-transparent px-3 text-sm text-rail-foreground placeholder:text-rail-subtle-foreground focus-visible:outline-none sm:px-0"
          {...form.register("name")}
        />
        <Button
          type="submit"
          variant="highlight"
          loading={createKey.pending}
          className={cn("w-full sm:w-auto", INK_FOCUS)}
        >
          {createKey.pending ? null : <KeyRound aria-hidden />}
          Create API key
        </Button>
      </div>
      {fieldError ? (
        <p id={errorId} className="flex items-start gap-1.5 text-xs font-medium text-rail-danger">
          <CircleAlert aria-hidden className="mt-px size-3.5 shrink-0" strokeWidth={2.25} />
          {fieldError}
        </p>
      ) : (
        <p id={hintId} className="text-sm text-rail-muted-foreground">
          Shown in Settings → API keys so you can revoke it later.
        </p>
      )}
      {formError ? (
        <Notice tone="danger" role="alert" className="mt-1">
          {formError}
        </Notice>
      ) : null}
    </form>
  );
}
