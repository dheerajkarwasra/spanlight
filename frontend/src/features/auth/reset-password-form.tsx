import { zodResolver } from "@hookform/resolvers/zod";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "@tanstack/react-router";
import { useState } from "react";
import { useForm } from "react-hook-form";
import { toast } from "sonner";
import { z } from "zod";

import { authApi, errorMessage, isApiError } from "@/lib/api";
import { applyServerFieldErrors } from "@/lib/form-errors";

import { Button, Callout, FormField, PasswordInput } from "./auth-controls";

const MIN_PASSWORD_LENGTH = 10;

const resetSchema = z
  .object({
    password: z
      .string()
      .min(MIN_PASSWORD_LENGTH, `Use at least ${MIN_PASSWORD_LENGTH} characters.`)
      .max(256, "Use at most 256 characters."),
    confirm: z.string().min(1, "Enter the new password again."),
  })
  .refine((values) => values.password === values.confirm, {
    path: ["confirm"],
    message: "The two passwords don't match.",
  });

type ResetValues = z.infer<typeof resetSchema>;

interface ResetPasswordFormProps {
  /** The secret from the emailed link. It is only sent to the reset route, never stored. */
  token: string;
  /** Called when the server says the link is unknown, used or expired. */
  onInvalidLink: () => void;
}

export function ResetPasswordForm({ token, onInvalidLink }: ResetPasswordFormProps) {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [formError, setFormError] = useState<string | null>(null);

  const form = useForm<ResetValues>({
    resolver: zodResolver(resetSchema),
    defaultValues: { password: "", confirm: "" },
  });

  const reset = useMutation({
    // The variables hold the new password: don't keep them in the mutation cache after use.
    gcTime: 0,
    mutationFn: (values: ResetValues) =>
      authApi.resetPassword({ token, password: values.password }),
    onSuccess: async () => {
      // Every session of the account just ended, this browser's included: forget what it knew.
      queryClient.clear();
      toast.success("Password updated. Sign in with your new password.");
      await navigate({ to: "/login", search: {} });
    },
    onError: (error) => {
      if (isApiError(error) && error.status === 404) {
        onInvalidLink();
        return;
      }
      if (!applyServerFieldErrors(error, form.setError, ["password"])) {
        setFormError(errorMessage(error));
      }
    },
  });

  const onSubmit = form.handleSubmit((values) => {
    setFormError(null);
    reset.mutate(values);
  });

  return (
    <form onSubmit={onSubmit} className="flex flex-col gap-6" noValidate>
      {formError ? (
        <Callout tone="danger" role="alert">
          {formError}
        </Callout>
      ) : null}

      <div className="flex flex-col gap-6">
        <FormField
          label="New password"
          error={form.formState.errors.password?.message}
          hint={`At least ${MIN_PASSWORD_LENGTH} characters. Resetting signs you out on every device.`}
        >
          <PasswordInput autoComplete="new-password" autoFocus {...form.register("password")} />
        </FormField>
        <FormField label="Confirm new password" error={form.formState.errors.confirm?.message}>
          <PasswordInput autoComplete="new-password" {...form.register("confirm")} />
        </FormField>
      </div>

      <Button type="submit" variant="primary" size="lg" loading={reset.isPending}>
        Set new password
      </Button>
    </form>
  );
}
