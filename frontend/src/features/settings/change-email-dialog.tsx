import { zodResolver } from "@hookform/resolvers/zod";
import { useLocation } from "@tanstack/react-router";
import { useState, type ComponentProps } from "react";
import { useForm } from "react-hook-form";
import { z } from "zod";

import { Callout } from "@/components/callout";
import { DialogHeading } from "@/components/dialog-heading";
import { DisabledReason } from "@/components/disabled-reason";
import { FormField } from "@/components/form-field";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogFooter, DialogTrigger } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { useMe } from "@/features/auth";
import { useSignOut } from "@/features/shell";
import { errorMessage, isApiError } from "@/lib/api";
import { applyServerFieldErrors } from "@/lib/form-errors";

import { isReauthRequired, useRequestEmailChange } from "./account-queries";

const changeEmailSchema = z.object({
  new_email: z.email("Enter a valid email address."),
  password: z.string(),
  code: z.string(),
});

type ChangeEmailValues = z.infer<typeof changeEmailSchema>;

/**
 * The two-factor code, asked when two-factor authentication is on. Shared with the delete dialog.
 * It takes recovery codes too (letters and a dash), so it is not a numeric field.
 */
export function AuthenticationCodeField({
  error,
  ...props
}: ComponentProps<typeof Input> & { error?: string | undefined }) {
  return (
    <FormField
      label="Authentication code"
      hint="6-digit code from your app, or a recovery code"
      error={error}
    >
      <Input
        autoComplete="one-time-code"
        autoCapitalize="none"
        autoCorrect="off"
        spellCheck={false}
        {...props}
      />
    </FormField>
  );
}

/**
 * "Sign in again to continue" (Figma "Change email — sign in again"), in place of the password for
 * an account that has none. Shared with the delete dialog.
 */
export function SignInAgainCallout({ action }: { action: string }) {
  // Back to this page once signed in again.
  const signOut = useSignOut(useLocation().href);
  return (
    <Callout
      tone="warning"
      role="alert"
      title="Sign in again to continue"
      action={
        <Button
          size="sm"
          loading={signOut.isPending}
          onClick={() => {
            signOut.mutate();
          }}
        >
          Sign out and sign in again
        </Button>
      }
    >
      Your account uses GitHub or Google. {action} needs a sign-in from the last 10 minutes.
    </Callout>
  );
}

/**
 * The "Change email" button and dialog (Figma "Change email"): the new address and the current
 * password (and the two-factor code when it is on), then the link goes to the new address.
 * Nothing changes until it is opened.
 */
export function ChangeEmailDialog() {
  const { has_password: hasPassword, totp_enabled: needsCode } = useMe();
  const [open, setOpen] = useState(false);
  const [sentTo, setSentTo] = useState<string | null>(null);
  const [reauth, setReauth] = useState(false);
  // Shown in the dialog: a toast would sit outside the modal, hidden from screen readers.
  const [formError, setFormError] = useState<string | null>(null);
  const request = useRequestEmailChange();
  const form = useForm<ChangeEmailValues>({
    resolver: zodResolver(changeEmailSchema),
    defaultValues: { new_email: "", password: "", code: "" },
  });

  function handleOpenChange(next: boolean) {
    if (request.isPending) {
      return;
    }
    setOpen(next);
    if (!next) {
      form.reset();
      setSentTo(null);
      setReauth(false);
      setFormError(null);
    }
  }

  const onSubmit = form.handleSubmit((values) => {
    if (hasPassword && !values.password) {
      form.setError("password", { message: "Enter your current password." });
      return;
    }
    const code = values.code.trim();
    if (needsCode && !code) {
      form.setError("code", {
        message: "Enter the code from your authenticator app, or a recovery code.",
      });
      return;
    }
    setFormError(null);
    request.mutate(
      {
        new_email: values.new_email,
        ...(hasPassword ? { password: values.password } : {}),
        ...(needsCode ? { code } : {}),
      },
      {
        onSuccess: () => {
          setSentTo(values.new_email);
        },
        onError: (error) => {
          if (isReauthRequired(error)) {
            setReauth(true);
          } else if (isApiError(error) && error.code === "INVALID_PASSWORD") {
            form.setError("password", { message: error.message }, { shouldFocus: true });
          } else if (
            isApiError(error) &&
            (error.code === "TOTP_CODE_REQUIRED" || error.code === "INVALID_TOTP_CODE")
          ) {
            form.setError("code", { message: error.message }, { shouldFocus: true });
          } else if (isApiError(error) && error.code === "EMAIL_TAKEN") {
            form.setError("new_email", { message: error.message }, { shouldFocus: true });
          } else if (
            !applyServerFieldErrors(error, form.setError, ["new_email", "password", "code"])
          ) {
            setFormError(errorMessage(error));
          }
        },
      },
    );
  });

  const sendButton = (
    <Button type="submit" variant="primary" disabled={reauth} loading={request.isPending}>
      Send link
    </Button>
  );

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogTrigger asChild>
        <Button size="lg">Change email</Button>
      </DialogTrigger>
      <DialogContent
        hideClose
        className="max-w-md gap-5 rounded-card p-5 sm:p-6"
        onOpenAutoFocus={(event) => {
          // The new address, not the close button: it is the first thing to fill in.
          event.preventDefault();
          form.setFocus("new_email");
        }}
      >
        {sentTo !== null ? (
          <>
            <DialogHeading
              title="Check your new inbox"
              description="The link goes to the new address only. We also tell your current address about the change."
            />
            <Callout tone="success" role="status">
              We sent a link to {sentTo}. Your address changes when you open it within an hour.
            </Callout>
            <DialogFooter>
              <Button
                variant="primary"
                onClick={() => {
                  handleOpenChange(false);
                }}
              >
                Done
              </Button>
            </DialogFooter>
          </>
        ) : (
          <>
            <DialogHeading
              title="Change email"
              description="We send a link to the new address. Your email changes when you open it."
              showClose={request.isPending ? "disabled" : true}
            />
            <form noValidate onSubmit={onSubmit} className="flex flex-col gap-5">
              <FormField label="New email" error={form.formState.errors.new_email?.message}>
                <Input
                  type="email"
                  autoComplete="email"
                  readOnly={request.isPending}
                  {...form.register("new_email")}
                />
              </FormField>
              {reauth ? (
                <SignInAgainCallout action="Changing your email" />
              ) : hasPassword ? (
                <FormField
                  label="Password"
                  hint="Your current password, to confirm it’s you."
                  error={form.formState.errors.password?.message}
                >
                  <Input
                    type="password"
                    autoComplete="current-password"
                    readOnly={request.isPending}
                    {...form.register("password")}
                  />
                </FormField>
              ) : null}
              {needsCode && !reauth ? (
                <AuthenticationCodeField
                  error={form.formState.errors.code?.message}
                  readOnly={request.isPending}
                  {...form.register("code")}
                />
              ) : null}
              {formError ? (
                <Callout tone="danger" role="alert">
                  {formError}
                </Callout>
              ) : null}
              <DialogFooter className="gap-2.5">
                <Button
                  onClick={() => {
                    handleOpenChange(false);
                  }}
                  disabled={request.isPending}
                >
                  Cancel
                </Button>
                {reauth ? (
                  <DisabledReason reason="Sign in again first.">{sendButton}</DisabledReason>
                ) : (
                  sendButton
                )}
              </DialogFooter>
            </form>
          </>
        )}
      </DialogContent>
    </Dialog>
  );
}
