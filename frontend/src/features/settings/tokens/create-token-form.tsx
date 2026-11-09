import { zodResolver } from "@hookform/resolvers/zod";
import { useEffect, useState } from "react";
import { useForm } from "react-hook-form";
import { toast } from "sonner";
import { z } from "zod";

import { Callout } from "@/components/callout";
import { DialogHeading } from "@/components/dialog-heading";
import { DisabledReason } from "@/components/disabled-reason";
import { FormField } from "@/components/form-field";
import { Button } from "@/components/ui/button";
import { DialogClose, DialogFooter } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { useMe } from "@/features/auth";
import {
  errorMessage,
  isApiError,
  type CreatedPersonalAccessToken,
  type TokenScope,
} from "@/lib/api";
import { applyServerFieldErrors } from "@/lib/form-errors";

import { isReauthRequired } from "../account-queries";
import { AuthenticationCodeField, SignInAgainCallout } from "../change-email-dialog";

import { ExpiryField } from "./expiry-field";
import { DEFAULT_EXPIRY, expiryTimestamp, type ExpiryChoice } from "./expiry";
import { ScopeChoice } from "./scope-choice";
import { DEFAULT_TOKEN_SCOPE } from "./scopes";
import { useCreateToken } from "./token-queries";

const TOKEN_NAME_MAX_LENGTH = 100;

const createTokenSchema = z.object({
  name: z
    .string()
    .trim()
    .min(1, "Enter a name for this token.")
    .max(TOKEN_NAME_MAX_LENGTH, `Use ${TOKEN_NAME_MAX_LENGTH} characters or fewer.`),
  password: z.string(),
  code: z.string(),
});

type CreateTokenValues = z.infer<typeof createTokenSchema>;

interface CreateTokenFormProps {
  /** Called as the request starts and ends, so the dialog can refuse to close in between. */
  onPendingChange: (pending: boolean) => void;
  /** Hands over the created token, whole secret included. The caller keeps it in state only. */
  onCreated: (token: CreatedPersonalAccessToken) => void;
}

/** Figma "Create token dialog": a name, Read or Write, and when it ends. */
export function CreateTokenForm({ onCreated, onPendingChange }: CreateTokenFormProps) {
  const { user, has_password: hasPassword, totp_enabled: needsCode } = useMe();
  const create = useCreateToken();
  const [reauth, setReauth] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);
  const [scope, setScope] = useState<TokenScope>(DEFAULT_TOKEN_SCOPE);
  const [expiry, setExpiry] = useState<ExpiryChoice>(DEFAULT_EXPIRY);
  const pending = create.pending;

  // A request that outlives the dialog would make a credential nobody sees.
  useEffect(() => {
    onPendingChange(pending);
    return () => {
      onPendingChange(false);
    };
  }, [pending, onPendingChange]);

  const form = useForm<CreateTokenValues>({
    resolver: zodResolver(createTokenSchema),
    defaultValues: { name: "", password: "", code: "" },
  });

  const onSubmit = form.handleSubmit(async (values) => {
    if (reauth) return;
    if (hasPassword && !values.password) {
      form.setError("password", { message: "Enter your current password." }, { shouldFocus: true });
      return;
    }
    const code = values.code.trim();
    if (needsCode && !code) {
      form.setError(
        "code",
        { message: "Enter the code from your authenticator app, or a recovery code." },
        { shouldFocus: true },
      );
      return;
    }
    setFormError(null);
    const result = await create.run({
      name: values.name,
      scope,
      expires_at: expiryTimestamp(expiry),
      ...(hasPassword ? { password: values.password } : {}),
      ...(needsCode ? { code } : {}),
    });
    if (result.ok) {
      onCreated(result.value);
      toast.success(`Created token "${result.value.name}".`);
    } else if (isReauthRequired(result.error)) {
      setReauth(true);
    } else if (isApiError(result.error) && result.error.code === "INVALID_PASSWORD") {
      form.setError("password", { message: result.error.message }, { shouldFocus: true });
    } else if (
      isApiError(result.error) &&
      (result.error.code === "TOTP_CODE_REQUIRED" || result.error.code === "INVALID_TOTP_CODE")
    ) {
      form.setError("code", { message: result.error.message }, { shouldFocus: true });
    } else if (!applyServerFieldErrors(result.error, form.setError, ["name", "password", "code"])) {
      setFormError(errorMessage(result.error));
    }
  });

  const createButton = (
    <Button type="submit" variant="primary" disabled={reauth} loading={pending}>
      Create token
    </Button>
  );

  return (
    <form onSubmit={onSubmit} noValidate className="grid gap-[22px]">
      <DialogHeading
        showClose={pending ? "disabled" : true}
        title="Create a token"
        description="Scripts and tools use it to call the Spanlight API as you."
      />
      <FormField
        label="Name"
        hint="Name it after where it's used, for example ci-release."
        error={form.formState.errors.name?.message}
      >
        <Input
          autoComplete="off"
          data-1p-ignore
          data-lpignore="true"
          autoFocus
          maxLength={TOKEN_NAME_MAX_LENGTH}
          placeholder="ci-release"
          {...form.register("name")}
        />
      </FormField>
      <ScopeChoice value={scope} onChange={setScope} />
      <ExpiryField value={expiry} onChange={setExpiry} />
      {hasPassword && !reauth ? (
        <>
          <input
            type="text"
            name="username"
            autoComplete="username"
            value={user.email}
            readOnly
            tabIndex={-1}
            aria-hidden="true"
            className="sr-only"
          />
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
        </>
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
      {reauth ? <SignInAgainCallout action="Creating a token" /> : null}
      <DialogFooter className="flex-row justify-end gap-2.5 max-sm:[&>*]:flex-1">
        <DialogClose asChild>
          <Button disabled={pending}>Cancel</Button>
        </DialogClose>
        {reauth ? (
          <DisabledReason reason="Sign in again first.">{createButton}</DisabledReason>
        ) : (
          createButton
        )}
      </DialogFooter>
    </form>
  );
}
