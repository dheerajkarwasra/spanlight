import { zodResolver } from "@hookform/resolvers/zod";
import { useMutation } from "@tanstack/react-query";
import { useState } from "react";
import { useForm } from "react-hook-form";
import { z } from "zod";

import { authApi, errorMessage, isApiError } from "@/lib/api";

import { Button, Callout, FormField, Input } from "./auth-controls";

const forgotSchema = z.object({ email: z.email("Enter a valid email address.") });

type ForgotValues = z.infer<typeof forgotSchema>;

/** What an answer other than "accepted" means for this screen. */
type Failure = { kind: "not-configured" } | { kind: "error"; message: string };

function toFailure(error: unknown): Failure {
  if (isApiError(error) && error.status === 409 && error.code === "NOT_CONFIGURED") {
    return { kind: "not-configured" };
  }
  if (isApiError(error) && error.status === 429) {
    return { kind: "error", message: "Too many reset requests. Wait 15 minutes and try again." };
  }
  return { kind: "error", message: errorMessage(error) };
}

interface ForgotPasswordFormProps {
  /** Called with the address once the server accepted the request. */
  onSent: (email: string) => void;
}

export function ForgotPasswordForm({ onSent }: ForgotPasswordFormProps) {
  const [failure, setFailure] = useState<Failure | null>(null);

  const form = useForm<ForgotValues>({
    resolver: zodResolver(forgotSchema),
    defaultValues: { email: "" },
  });

  const forgot = useMutation({
    mutationFn: (values: ForgotValues) => authApi.forgotPassword(values.email),
    onSuccess: (_accepted, values) => {
      onSent(values.email);
    },
    onError: (error) => {
      setFailure(toFailure(error));
    },
  });

  const onSubmit = form.handleSubmit((values) => {
    setFailure(null);
    forgot.mutate(values);
  });

  return (
    <form onSubmit={onSubmit} className="flex flex-col gap-6" noValidate>
      <FormField label="Email" error={form.formState.errors.email?.message}>
        <Input type="email" autoComplete="email" autoFocus {...form.register("email")} />
      </FormField>

      <Button type="submit" variant="primary" size="lg" loading={forgot.isPending}>
        Send reset link
      </Button>

      {failure?.kind === "not-configured" ? (
        <Callout tone="warning" title="Not available on this server" role="status">
          Email isn&apos;t set up on this server, so reset links can&apos;t be sent. Ask your
          administrator to run{" "}
          <code className="font-mono font-medium">spanlight reset-password</code>.
        </Callout>
      ) : null}
      {failure?.kind === "error" ? (
        <Callout tone="danger" role="alert">
          {failure.message}
        </Callout>
      ) : null}
    </form>
  );
}
