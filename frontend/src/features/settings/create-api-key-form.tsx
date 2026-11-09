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
import { type CreatedApiKey, type KeyScope } from "@/lib/api";
import { applyServerFieldErrors } from "@/lib/form-errors";

import { ApiKeyScopeField } from "./api-key-scope-field";
import { useCreateApiKey } from "./api-key-queries";
import { hasReadKeyScope } from "./api-key-utils";
import { MemberReauthFields } from "./member-reauth";
import { useMemberReauth } from "./use-member-reauth";
import { DEFAULT_EXPIRY, expiryTimestamp, type ExpiryChoice } from "./tokens/expiry";
import { ExpiryField } from "./tokens/expiry-field";
import { DEFAULT_KEY_SCOPES, hasKeyScope } from "./tokens/scopes";

const KEY_NAME_MAX_LENGTH = 100;

const createKeySchema = z.object({
  name: z
    .string()
    .trim()
    .min(1, "Enter a name for this key.")
    .max(KEY_NAME_MAX_LENGTH, `Use ${KEY_NAME_MAX_LENGTH} characters or fewer.`),
});

type CreateKeyValues = z.infer<typeof createKeySchema>;

interface CreateApiKeyFormProps {
  /** Called as the request starts and ends, so the dialog can refuse to close in between. */
  onPendingChange: (pending: boolean) => void;
  /** Hands over the created key, secret included. The caller keeps it in state only. */
  onCreated: (key: CreatedApiKey) => void;
}

/** Figma "API keys v2 — Create key dialog": a name, the scopes and when the key ends. */
export function CreateApiKeyForm({ onCreated, onPendingChange }: CreateApiKeyFormProps) {
  const createKey = useCreateApiKey();
  const confirmation = useMemberReauth();
  const { reauth, formError } = confirmation;
  const [scopes, setScopes] = useState<KeyScope[]>([...DEFAULT_KEY_SCOPES]);
  const needsProof = hasReadKeyScope(scopes);
  const [expiry, setExpiry] = useState<ExpiryChoice>(DEFAULT_EXPIRY);
  const pending = createKey.pending;

  // A request that outlives the dialog would make a credential nobody sees.
  useEffect(() => {
    onPendingChange(pending);
    return () => {
      onPendingChange(false);
    };
  }, [pending, onPendingChange]);

  const form = useForm<CreateKeyValues>({
    resolver: zodResolver(createKeySchema),
    defaultValues: { name: "" },
  });

  const onSubmit = form.handleSubmit(async (values) => {
    if (pending || !hasKeyScope(scopes)) return;
    const proof = needsProof ? confirmation.proof(confirmation.form.getValues()) : {};
    if (!proof) return;
    const result = await createKey.run({
      name: values.name,
      scopes,
      expires_at: expiryTimestamp(expiry),
      ...proof,
    });
    if (result.ok) {
      onCreated(result.value);
      toast.success(`Created API key "${result.value.name}".`);
    } else if (
      !applyServerFieldErrors<CreateKeyValues>(
        result.error,
        (name, error) => form.setError(name, error, { shouldFocus: true }),
        ["name"],
      )
    ) {
      confirmation.onError(result.error);
    }
  });

  const createButton = (
    <Button
      type="submit"
      variant="primary"
      loading={pending}
      disabled={!hasKeyScope(scopes) || (needsProof && reauth)}
    >
      Create key
    </Button>
  );

  return (
    <form onSubmit={onSubmit} noValidate className="grid gap-[22px]">
      <DialogHeading
        showClose={pending ? "disabled" : true}
        title="Create API key"
        description="For an application that sends traces to this project, or reads them through the API."
      />
      <FormField
        label="Name"
        hint="Name it after where it's used, for example production-api."
        error={form.formState.errors.name?.message}
      >
        <Input
          autoComplete="off"
          data-1p-ignore
          data-lpignore="true"
          autoFocus
          maxLength={KEY_NAME_MAX_LENGTH}
          placeholder="production-api"
          {...form.register("name")}
        />
      </FormField>
      <ApiKeyScopeField
        selected={scopes}
        onChange={(next) => {
          setScopes(next);
          if (!hasReadKeyScope(next)) confirmation.reset();
        }}
      />
      <ExpiryField value={expiry} onChange={setExpiry} />
      <p aria-live="polite" className="empty:sr-only">
        {needsProof ? (
          <>
            <span className="text-sm font-semibold text-foreground">Confirm it's you.</span>
            <span className="text-xs text-muted-foreground">
              {" A key that can read sees this project's data for as long as it lives."}
            </span>
          </>
        ) : null}
      </p>
      {needsProof ? (
        <MemberReauthFields
          confirmation={confirmation}
          pending={pending}
          action="Creating a key that can read"
        />
      ) : null}
      {formError ? (
        <Callout tone="danger" role="alert">
          {formError}
        </Callout>
      ) : null}
      <DialogFooter className="flex-row justify-end gap-2.5 max-sm:[&>*]:flex-1">
        <DialogClose asChild>
          <Button disabled={pending}>Cancel</Button>
        </DialogClose>
        {needsProof && reauth ? (
          <DisabledReason reason="Sign in again first.">{createButton}</DisabledReason>
        ) : (
          createButton
        )}
      </DialogFooter>
    </form>
  );
}
