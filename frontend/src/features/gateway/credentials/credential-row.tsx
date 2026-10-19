import { Trash2 } from "lucide-react";
import { useId } from "react";
import { toast } from "sonner";

import { CopyButton } from "@/components/copy-button";
import { Button } from "@/components/ui/button";
import { UnknownValue } from "@/components/unknown-value";
import { errorMessage, type Credential, type Route } from "@/lib/api";
import { formatRelativeTime, formatTimestamp } from "@/lib/format";

import { CheckStatus } from "./check-status";
import { CREDENTIAL_COLUMNS } from "./credential-columns";
import { PROVIDER_LABELS } from "./credential-form";
import { baseUrlLabel, usedByLabel, type RouteUse } from "./credential-model";
import { useCheckCredential } from "./credentials-queries";
import { DeleteCredentialDialog } from "./delete-credential-dialog";
import { RotateDialog } from "./rotate-dialog";

interface CredentialRowProps {
  credential: Credential;
  /** Owners only. Everyone else gets no actions; the page's banner says why. */
  canManage: boolean;
  /** Routes of this project that target the credential, when any do. */
  uses: readonly RouteUse[] | undefined;
  routes: readonly Route[];
  onNotConfigured: () => void;
}

/**
 * Figma "Gateway/Credential row" and its mobile tile (182:3285): name, provider and credential ID
 * with a copy button (the eval-gate Action takes that ID), base URL, last use, last check, and
 * Check, Rotate and Delete. Below the container breakpoint the tile stacks
 * those with overline labels and puts the actions under them. A credential a route uses shows
 * "Used by route {route}" and its Delete stays disabled, with that text as the reason.
 */
export function CredentialRow({
  credential,
  canManage,
  uses,
  routes,
  onNotConfigured,
}: CredentialRowProps) {
  const check = useCheckCredential();
  const usedById = useId();
  const usedBy = usedByLabel(uses);
  const label = baseUrlLabel(credential);
  const host = label ?? <UnknownValue reason="This server has no base URL stored" />;

  return (
    <li className="flex flex-col gap-3 rounded-tile bg-surface-muted p-4 @[44rem]:flex-row @[44rem]:items-center @[44rem]:py-2.5 @[44rem]:pr-3 @[44rem]:pl-4">
      {/* Announces the outcome of a check; always present so the change is read out. */}
      <span role="status" className="sr-only">
        {checkAnnouncement(credential.name, check)}
      </span>
      <div className={CREDENTIAL_COLUMNS.name}>
        <span
          title={credential.name}
          className="block truncate text-sm font-semibold text-foreground"
        >
          {credential.name}
        </span>
        <span className="block text-xs font-medium text-subtle-foreground">
          {PROVIDER_LABELS[credential.provider]}
        </span>
        <span className="flex min-w-0 items-center gap-0.5">
          <code
            title={credential.id}
            className="truncate font-mono text-label text-muted-foreground"
          >
            {credential.id}
          </code>
          <CopyButton
            value={credential.id}
            label={`Copy ${credential.name} ID`}
            className="size-7 shrink-0"
          />
        </span>
        {usedBy ? (
          <span
            aria-hidden
            className="mt-0.5 hidden text-xs font-medium text-subtle-foreground @[44rem]:block"
          >
            {usedBy}
          </span>
        ) : null}
      </div>
      {/* Narrow tile: labelled pairs, then the check chip. */}
      <div className="flex flex-col gap-3 @[44rem]:hidden">
        <div className="grid grid-cols-2 gap-3">
          <div className="min-w-0">
            <p className="text-overline text-subtle-foreground uppercase">Base URL</p>
            <p
              title={credential.base_url ?? undefined}
              className="truncate font-mono text-xs text-muted-foreground"
            >
              {host}
            </p>
          </div>
          <div className="min-w-0">
            <p className="text-overline text-subtle-foreground uppercase">Last used</p>
            <p className="text-sm text-muted-foreground">
              <LastUsed credential={credential} />
            </p>
          </div>
        </div>
        <CheckStatus credential={credential} checking={check.isPending} className="self-start" />
      </div>
      <div className={CREDENTIAL_COLUMNS.baseUrl}>
        <span className="sr-only">Base URL </span>
        <span title={credential.base_url ?? undefined} className="block truncate font-mono text-xs">
          {host}
        </span>
      </div>
      <div className={`${CREDENTIAL_COLUMNS.lastUsed} text-sm text-muted-foreground`}>
        <span className="sr-only">Last used </span>
        <LastUsed credential={credential} />
      </div>
      <div className={CREDENTIAL_COLUMNS.check}>
        <span className="sr-only">Last check </span>
        <CheckStatus credential={credential} checking={check.isPending} />
      </div>
      <div className={CREDENTIAL_COLUMNS.actions}>
        {canManage ? (
          <>
            <div className="flex items-center gap-1.5">
              <Button
                size="sm"
                disabled={check.isPending}
                aria-label={`Check ${credential.name}`}
                onClick={() => {
                  check.mutate(credential.id, {
                    onError: (error) => {
                      toast.error(errorMessage(error));
                    },
                  });
                }}
              >
                Check
              </Button>
              <RotateDialog
                credential={credential}
                onNotConfigured={onNotConfigured}
                trigger={
                  <Button size="sm" aria-label={`Rotate ${credential.name}`}>
                    Rotate
                  </Button>
                }
              />
            </div>
            <DeleteCredentialDialog
              credential={credential}
              routes={routes}
              trigger={
                <Button
                  size="icon-sm"
                  disabled={usedBy !== null}
                  aria-label={`Delete ${credential.name}`}
                  aria-describedby={usedBy ? usedById : undefined}
                >
                  <Trash2 aria-hidden />
                </Button>
              }
            />
          </>
        ) : null}
      </div>
      {usedBy ? (
        <p id={usedById} className="text-xs font-medium text-subtle-foreground @[44rem]:sr-only">
          {usedBy}
        </p>
      ) : null}
    </li>
  );
}

function checkAnnouncement(name: string, check: ReturnType<typeof useCheckCredential>): string {
  if (check.isPending) {
    return `Checking ${name}`;
  }
  if (check.data) {
    return check.data.status === "ok"
      ? `${name} is working`
      : `${name} is failing: ${check.data.error ?? "unknown error"}`;
  }
  return "";
}

function LastUsed({ credential }: { credential: Credential }) {
  if (credential.last_used_at === null) {
    return <span className="text-subtle-foreground">Never</span>;
  }
  return (
    <time
      dateTime={credential.last_used_at}
      title={formatTimestamp(credential.last_used_at) ?? undefined}
      className="tabular"
    >
      {formatRelativeTime(credential.last_used_at) ?? credential.last_used_at}
    </time>
  );
}
