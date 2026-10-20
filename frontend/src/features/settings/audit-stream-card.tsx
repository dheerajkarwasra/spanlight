import { useState, type SyntheticEvent } from "react";
import { toast } from "sonner";

import { ConfirmDialog } from "@/components/confirm-dialog";
import { FormField } from "@/components/form-field";
import { RelativeTime } from "@/components/relative-time";
import { SecretBox } from "@/components/secret-box";
import { SectionCard } from "@/components/section-card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { Switch } from "@/components/ui/switch";
import { useCurrentRole } from "@/features/shell";
import { ApiError, errorMessage, type AuditStream } from "@/lib/api";
import { can, rolesWith } from "@/lib/permissions";

import { useAuditStreamMutations, useAuditStreamQuery } from "./audit-stream-queries";

const DESCRIPTION =
  "Sends every audit event to your log tool as signed JSON over HTTPS, in order. Only owners can change it.";

/** Figma "Audit log v3" Streaming card: not set up, on (with the secret once), failing. */
export function AuditStreamCard() {
  const query = useAuditStreamQuery();
  const role = useCurrentRole();
  const canEdit = can(role, "org:security");
  const mutations = useAuditStreamMutations();
  // The signing secret, kept in this component only, from the save or rotation that made it.
  const [secret, setSecret] = useState<string | null>(null);

  let body;
  if (query.isPending) {
    body = <Skeleton className="h-10 w-full" />;
  } else if (query.isError) {
    body = <p className="text-sm text-danger-text">{errorMessage(query.error)}</p>;
  } else if (query.data === null) {
    body = canEdit ? (
      <StreamForm
        pending={mutations.save.isPending}
        onSubmit={async (url) => {
          const saved = await mutations.save.mutateAsync({ url, enabled: true });
          setSecret(saved.secret);
          toast.success("Streaming started.");
        }}
      />
    ) : (
      <p className="text-sm text-muted-foreground">
        No stream is set up. {rolesWith("org:security")} can set one up.
      </p>
    );
  } else {
    body = (
      <StreamDetails
        stream={query.data}
        canEdit={canEdit}
        secret={secret}
        mutations={mutations}
        onSecret={setSecret}
      />
    );
  }

  const stream = query.data ?? null;
  return (
    <SectionCard
      title="Streaming"
      description={DESCRIPTION}
      actions={
        stream ? (
          <Switch
            aria-label="Stream audit events"
            checked={stream.enabled}
            disabled={!canEdit || mutations.save.isPending}
            onCheckedChange={(enabled) => {
              mutations.save.mutate(
                { url: stream.url, enabled },
                { onError: (error) => toast.error(errorMessage(error)) },
              );
            }}
          />
        ) : null
      }
      className="gap-3.5"
    >
      {body}
    </SectionCard>
  );
}

interface StreamFormProps {
  pending: boolean;
  onSubmit: (url: string) => Promise<void>;
}

function StreamForm({ pending, onSubmit }: StreamFormProps) {
  const [url, setUrl] = useState("");
  const [error, setError] = useState<string | undefined>(undefined);

  async function handleSubmit(event: SyntheticEvent) {
    event.preventDefault();
    setError(undefined);
    try {
      await onSubmit(url.trim());
    } catch (failure) {
      const field =
        failure instanceof ApiError
          ? failure.fieldErrors.find((e) => e.field === "url")
          : undefined;
      setError(field?.message ?? errorMessage(failure));
    }
  }

  return (
    <form onSubmit={(event) => void handleSubmit(event)} className="flex flex-col gap-2">
      <div className="flex flex-col gap-2 sm:flex-row sm:items-end">
        <FormField label="Endpoint URL" error={error} className="min-w-0 flex-1">
          <Input
            type="url"
            inputMode="url"
            autoComplete="off"
            placeholder="https://"
            required
            value={url}
            onChange={(event) => {
              setUrl(event.target.value);
            }}
          />
        </FormField>
        <Button type="submit" variant="primary" loading={pending} disabled={url.trim() === ""}>
          Start streaming
        </Button>
      </div>
      <p className="text-xs font-medium text-muted-foreground">
        It starts with the events written after you save, and signs each request like alert webhooks
        do.
      </p>
    </form>
  );
}

interface StreamDetailsProps {
  stream: AuditStream;
  canEdit: boolean;
  secret: string | null;
  mutations: ReturnType<typeof useAuditStreamMutations>;
  onSecret: (secret: string | null) => void;
}

function StreamDetails({ stream, canEdit, secret, mutations, onSecret }: StreamDetailsProps) {
  const pending = Math.max(0, stream.head_seq - stream.delivered_seq);
  const failing = stream.consecutive_failures > 0;

  return (
    <div className="flex flex-col gap-3.5">
      {secret ? (
        <div className="flex flex-col gap-2 rounded-[10px] bg-surface-selected p-3.5">
          <p className="text-sm font-semibold text-foreground">
            Copy the signing secret now. It is not shown again.
          </p>
          <SecretBox label="Signing secret" secret={secret} copyAriaLabel="Copy signing secret" />
          <p className="text-xs font-medium text-muted-foreground">
            Verify each request: HMAC-SHA256 of {'"<X-Spanlight-Timestamp>." + body'}, sent as
            X-Spanlight-Signature.
          </p>
        </div>
      ) : null}
      <dl className="grid grid-cols-[auto_minmax(0,1fr)] items-center gap-x-4 gap-y-2 text-xs font-medium">
        <dt className="w-24 text-muted-foreground">Endpoint</dt>
        <dd className="truncate font-mono text-label text-foreground" title={stream.url}>
          {stream.url}
        </dd>
        <dt className="text-muted-foreground">Status</dt>
        <dd className="flex min-w-0 flex-wrap items-center gap-2 text-muted-foreground">
          <StreamStatus stream={stream} pending={pending} />
        </dd>
      </dl>
      {failing && pending > 0 ? (
        <p className="rounded-[10px] bg-danger-subtle px-3 py-2.5 text-xs font-medium text-danger-text">
          {pending.toLocaleString()} {pending === 1 ? "event is" : "events are"} waiting. Nothing is
          lost: delivery resumes from the first one the endpoint has not accepted.
        </p>
      ) : null}
      {canEdit ? (
        <div className="flex flex-wrap items-center gap-2">
          <Button
            variant="secondary"
            loading={mutations.test.isPending}
            onClick={() => {
              mutations.test.mutate(undefined, {
                onSuccess: (result) => {
                  if (result.ok) {
                    toast.success("The endpoint accepted the test event.");
                  } else {
                    toast.error(`The test failed: ${result.error ?? "no answer"}`);
                  }
                },
                onError: (error) => toast.error(errorMessage(error)),
              });
            }}
          >
            Send test
          </Button>
          <Button
            variant="ghost"
            loading={mutations.rotate.isPending}
            onClick={() => {
              mutations.rotate.mutate(undefined, {
                onSuccess: (result) => {
                  onSecret(result.secret);
                },
                onError: (error) => toast.error(errorMessage(error)),
              });
            }}
          >
            Rotate secret
          </Button>
          <ConfirmDialog
            trigger={
              <Button variant="danger" className="ml-auto">
                Remove
              </Button>
            }
            title="Stop streaming?"
            description="The endpoint and its signing secret are deleted. Events stay in the audit log."
            confirmLabel="Remove"
            onConfirm={async () => {
              try {
                await mutations.remove.mutateAsync();
                onSecret(null);
              } catch (error) {
                toast.error(errorMessage(error));
                throw error;
              }
            }}
          />
        </div>
      ) : null}
    </div>
  );
}

function StreamStatus({ stream, pending }: { stream: AuditStream; pending: number }) {
  if (!stream.enabled) {
    return (
      <>
        <Badge variant="neutral" size="sm">
          Paused
        </Badge>
        <span>
          {pending > 0 ? `${pending.toLocaleString()} waiting` : "Nothing waiting"}; turn it on to
          resume.
        </span>
      </>
    );
  }
  if (stream.consecutive_failures > 0) {
    return (
      <>
        <Badge variant="danger" size="sm">
          Failing
        </Badge>
        <span className="min-w-0 break-words">
          {stream.last_error ?? "The endpoint did not accept the events"} ·{" "}
          {stream.consecutive_failures} failed {stream.consecutive_failures === 1 ? "try" : "tries"}
          {stream.last_error_at ? (
            <>
              {" "}
              · last <RelativeTime iso={stream.last_error_at} />
            </>
          ) : null}
        </span>
      </>
    );
  }
  return (
    <>
      <Badge variant="success" size="sm">
        Delivering
      </Badge>
      <span>
        {stream.last_delivered_at ? (
          <>
            Last sent <RelativeTime iso={stream.last_delivered_at} />
          </>
        ) : (
          "Nothing sent yet"
        )}{" "}
        · {pending > 0 ? `${pending.toLocaleString()} waiting` : "nothing waiting"}
      </span>
    </>
  );
}
