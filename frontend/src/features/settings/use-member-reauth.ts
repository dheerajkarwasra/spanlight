import { useCallback, useState } from "react";
import { useForm } from "react-hook-form";

import { useMe } from "@/features/auth";
import { isApiError } from "@/lib/api";

import { isReauthRequired } from "./account-queries";
import { memberErrorMessage } from "./member-utils";

interface MemberReauthValues {
  password: string;
  code: string;
  invitee: string;
}

export function useMemberReauth() {
  const me = useMe();
  const [reauth, setReauth] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);
  const form = useForm<MemberReauthValues>({
    defaultValues: { password: "", code: "", invitee: "" },
  });

  function proof(values: MemberReauthValues) {
    if (reauth) return null;
    if (me.has_password && !values.password) {
      form.setError("password", { message: "Enter your current password." }, { shouldFocus: true });
      return null;
    }
    const code = values.code.trim();
    if (me.totp_enabled && !code) {
      form.setError(
        "code",
        { message: "Enter the code from your authenticator app, or a recovery code." },
        { shouldFocus: true },
      );
      return null;
    }
    setFormError(null);
    return {
      ...(me.has_password ? { password: values.password } : {}),
      ...(me.totp_enabled ? { code } : {}),
    };
  }

  function onError(error: unknown) {
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
      let applied = false;
      if (isApiError(error)) {
        for (const field of error.fieldErrors) {
          const name = field.field === "email" ? "invitee" : field.field;
          if (name === "invitee" || name === "password" || name === "code") {
            form.setError(name, { message: field.message }, { shouldFocus: !applied });
            applied = true;
          }
        }
      }
      if (!applied) setFormError(memberErrorMessage(error));
    }
  }

  const reset = useCallback(() => {
    form.resetField("password");
    form.resetField("code");
    setReauth(false);
    setFormError(null);
  }, [form]);

  return { form, me, reauth, formError, proof, onError, reset };
}
