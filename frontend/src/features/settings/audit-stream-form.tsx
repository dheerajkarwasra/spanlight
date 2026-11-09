import { useState } from "react";

import { Callout } from "@/components/callout";
import { FormField } from "@/components/form-field";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { ApiError } from "@/lib/api";
import type { AuditStreamProof } from "@/lib/api/orgs";

import { MemberReauthFields } from "./member-reauth";
import { useMemberReauth } from "./use-member-reauth";

export function StreamForm({
  pending,
  onSubmit,
}: {
  pending: boolean;
  onSubmit: (url: string, proof: AuditStreamProof) => Promise<void>;
}) {
  const [url, setUrl] = useState("");
  const [urlError, setUrlError] = useState<string | undefined>();
  const confirmation = useMemberReauth();
  const { form, reauth, formError } = confirmation;
  const submit = form.handleSubmit(async (values) => {
    if (pending) return;
    setUrlError(undefined);
    const proof = confirmation.proof(values);
    if (!proof) return;
    try {
      await onSubmit(url.trim(), proof);
    } catch (error) {
      const field =
        error instanceof ApiError
          ? error.fieldErrors.find((entry) => entry.field === "url")
          : undefined;
      if (field) setUrlError(field.message);
      else confirmation.onError(error);
    }
  });

  return (
    <form onSubmit={submit} className="flex flex-col gap-3.5">
      <FormField label="Endpoint URL" error={urlError}>
        <Input
          type="url"
          inputMode="url"
          autoComplete="off"
          placeholder="https://"
          required
          readOnly={pending}
          value={url}
          onChange={(event) => setUrl(event.target.value)}
        />
      </FormField>
      <p className="text-xs font-medium text-muted-foreground">
        It starts with the events written after you save, and signs each request like alert webhooks
        do.
      </p>
      <p className="text-sm font-semibold text-foreground">Confirm it's you.</p>
      <MemberReauthFields
        confirmation={confirmation}
        pending={pending}
        action="Changing audit streaming"
      />
      {formError ? (
        <Callout tone="danger" role="alert">
          {formError}
        </Callout>
      ) : null}
      <Button
        type="submit"
        variant="primary"
        loading={pending}
        disabled={reauth || url.trim() === ""}
        className="self-start"
      >
        Start streaming
      </Button>
    </form>
  );
}
