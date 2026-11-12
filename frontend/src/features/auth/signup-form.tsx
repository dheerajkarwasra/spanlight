import { zodResolver } from "@hookform/resolvers/zod";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "@tanstack/react-router";
import { useState } from "react";
import { useForm } from "react-hook-form";
import { z } from "zod";

import { authApi, errorMessage, isApiError, queryKeys } from "@/lib/api";
import { applyServerFieldErrors } from "@/lib/form-errors";

import { Button, Callout, FormField, Input, PasswordInput } from "./auth-controls";
import { refreshMe, useShowsTermsLine } from "./queries";
import { safeNextPath } from "./search";
import { TermsLine } from "./terms-line";

const MIN_PASSWORD_LENGTH = 10;

const signupSchema = z.object({
  name: z.string().trim().min(1, "Enter your name.").max(100, "Use at most 100 characters."),
  email: z.email("Enter a valid email address."),
  password: z
    .string()
    .min(MIN_PASSWORD_LENGTH, `Use at least ${MIN_PASSWORD_LENGTH} characters.`)
    .max(256, "Use at most 256 characters."),
});

type SignupValues = z.infer<typeof signupSchema>;

const SIGNUP_FIELDS = ["name", "email", "password"] as const;

interface SignupFormProps {
  /** The page to land on after signing up, as it came in the URL. */
  next: string | undefined;
}

export function SignupForm({ next }: SignupFormProps) {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [formError, setFormError] = useState<string | null>(null);
  const showsTerms = useShowsTermsLine();

  const form = useForm<SignupValues>({
    resolver: zodResolver(signupSchema),
    defaultValues: { name: "", email: "", password: "" },
  });

  const signup = useMutation({
    // The variables hold the new password: don't keep them in the mutation cache after use.
    gcTime: 0,
    mutationFn: authApi.signup,
    onSuccess: async () => {
      await refreshMe(queryClient);
      const destination = safeNextPath(next);
      if (destination) {
        await navigate({ href: destination });
      } else {
        await navigate({ to: "/onboarding", search: {} });
      }
    },
    onError: (error) => {
      // Submitted before the terms lookup answered: load it, so the line shows and the next
      // submit carries the agreement.
      if (isApiError(error) && error.code === "TERMS_NOT_ACCEPTED") {
        void queryClient.invalidateQueries({ queryKey: queryKeys.authTerms });
      }
      if (!applyServerFieldErrors(error, form.setError, SIGNUP_FIELDS)) {
        setFormError(errorMessage(error));
      }
    },
  });

  const onSubmit = form.handleSubmit((values) => {
    setFormError(null);
    // Creating the account under the terms line is the agreement; no line, nothing agreed.
    signup.mutate(showsTerms ? { ...values, accept_terms: true } : values);
  });

  return (
    <form onSubmit={onSubmit} className="flex flex-col gap-[18px]" noValidate>
      {formError ? (
        <Callout tone="danger" role="alert">
          {formError}
        </Callout>
      ) : null}

      <div className="flex flex-col gap-[18px]">
        <FormField label="Name" error={form.formState.errors.name?.message}>
          <Input autoComplete="name" autoFocus {...form.register("name")} />
        </FormField>
        <FormField label="Work email" error={form.formState.errors.email?.message}>
          <Input type="email" autoComplete="email" {...form.register("email")} />
        </FormField>
        <FormField
          label="Password"
          error={form.formState.errors.password?.message}
          hint={`At least ${MIN_PASSWORD_LENGTH} characters.`}
        >
          <PasswordInput autoComplete="new-password" {...form.register("password")} />
        </FormField>
      </div>

      <div className="flex flex-col gap-2.5">
        <Button type="submit" variant="primary" size="lg" loading={signup.isPending}>
          Create account
        </Button>
        <TermsLine />
      </div>
    </form>
  );
}
