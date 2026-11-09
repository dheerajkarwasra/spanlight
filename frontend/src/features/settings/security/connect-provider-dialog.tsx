import { zodResolver } from "@hookform/resolvers/zod";
import { useMutation } from "@tanstack/react-query";
import { useEffect, useState, type ReactNode } from "react";
import { useForm } from "react-hook-form";
import { z } from "zod";

import { Callout } from "@/components/callout";
import { DialogHeading } from "@/components/dialog-heading";
import { DisabledReason } from "@/components/disabled-reason";
import { FormField } from "@/components/form-field";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogFooter } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { rememberOAuthProvider, useMe } from "@/features/auth";
import { errorMessage, isApiError, securityApi, type OAuthProvider } from "@/lib/api";

import { PROVIDER_LABELS } from "./sign-in-methods";
import { isReauthRequired } from "../account-queries";
import { AuthenticationCodeField, SignInAgainCallout } from "../change-email-dialog";

const schema = z.object({ password: z.string(), code: z.string() });

export function ConnectProviderDialog({
  provider,
  next,
  children,
}: {
  provider: OAuthProvider;
  next: string;
  children: ReactNode;
}) {
  const { has_password: hasPassword, totp_enabled: needsCode } = useMe();
  const label = PROVIDER_LABELS[provider];
  const [open, setOpen] = useState(false);
  const [reauth, setReauth] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);
  const form = useForm<z.infer<typeof schema>>({
    resolver: zodResolver(schema),
    defaultValues: { password: "", code: "" },
  });
  const request = useMutation({
    mutationFn: (input: Parameters<typeof securityApi.linkOAuth>[1]) =>
      securityApi.linkOAuth(provider, input),
    onSuccess: ({ url }) => {
      rememberOAuthProvider(provider);
      window.location.href = url;
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
      } else {
        setFormError(errorMessage(error));
      }
    },
  });
  const pending = request.isPending || request.isSuccess;

  // Back from the provider restores this page from the browser's cache, still pending for good.
  const { reset } = request;
  useEffect(() => {
    const restored = (event: PageTransitionEvent) => {
      if (event.persisted) {
        reset();
      }
    };
    window.addEventListener("pageshow", restored);
    return () => {
      window.removeEventListener("pageshow", restored);
    };
  }, [reset]);

  function handleOpenChange(value: boolean) {
    if (pending) return;
    setOpen(value);
    if (!value) {
      form.reset();
      request.reset();
      setReauth(false);
      setFormError(null);
    }
  }

  const onSubmit = form.handleSubmit((values) => {
    if (pending || reauth) return;
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
    request.mutate({
      next,
      ...(hasPassword ? { password: values.password } : {}),
      ...(needsCode ? { code } : {}),
    });
  });

  const submitButton = (
    <Button type="submit" variant="primary" disabled={reauth} loading={pending}>
      Continue to {label}
    </Button>
  );

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      {children}
      <DialogContent
        hideClose
        className="max-w-md gap-5 rounded-card p-5 sm:p-6"
        onOpenAutoFocus={(event) => {
          if (hasPassword || needsCode) {
            event.preventDefault();
            form.setFocus(hasPassword ? "password" : "code");
          }
        }}
      >
        <DialogHeading
          title={`Connect ${label}`}
          description={`Anyone who can sign in to this ${label} account gets into your Spanlight account.${hasPassword || needsCode ? " Confirm it's you first." : ""}`}
          showClose={pending ? "disabled" : true}
        />
        <form noValidate onSubmit={onSubmit} className="flex flex-col gap-5">
          {reauth ? (
            <SignInAgainCallout action={`Connecting ${label}`} />
          ) : hasPassword ? (
            <FormField
              label="Password"
              hint="Your current password, to confirm it's you."
              error={form.formState.errors.password?.message}
            >
              <Input
                type="password"
                autoComplete="current-password"
                readOnly={pending}
                {...form.register("password")}
              />
            </FormField>
          ) : null}
          {needsCode && !reauth ? (
            <AuthenticationCodeField
              error={form.formState.errors.code?.message}
              readOnly={pending}
              {...form.register("code")}
            />
          ) : null}
          {formError ? (
            <Callout tone="danger" role="alert">
              {formError}
            </Callout>
          ) : null}
          <DialogFooter className="gap-2.5">
            <Button onClick={() => handleOpenChange(false)} disabled={pending}>
              Cancel
            </Button>
            {reauth ? (
              <DisabledReason reason="Sign in again first.">{submitButton}</DisabledReason>
            ) : (
              submitButton
            )}
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
