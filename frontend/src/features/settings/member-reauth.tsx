import { FormField } from "@/components/form-field";
import { Input } from "@/components/ui/input";

import { AuthenticationCodeField, SignInAgainCallout } from "./change-email-dialog";
import type { useMemberReauth } from "./use-member-reauth";

export function MemberReauthFields({
  confirmation,
  pending,
  action,
}: {
  confirmation: ReturnType<typeof useMemberReauth>;
  pending: boolean;
  action: string;
}) {
  const { form, me, reauth } = confirmation;
  if (reauth) return <SignInAgainCallout action={action} />;
  return (
    <>
      {me.has_password ? (
        <>
          <input
            type="text"
            name="username"
            data-1p-ignore
            data-lpignore="true"
            autoComplete="username"
            value={me.user.email}
            readOnly
            tabIndex={-1}
            aria-hidden="true"
            className="sr-only"
          />
          <FormField
            label="Password"
            hint="Your current password, to confirm it’s you."
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
      {me.totp_enabled ? (
        <AuthenticationCodeField
          error={form.formState.errors.code?.message}
          readOnly={pending}
          {...form.register("code")}
        />
      ) : null}
    </>
  );
}
