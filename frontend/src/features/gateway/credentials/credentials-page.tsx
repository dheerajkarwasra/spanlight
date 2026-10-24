import { KeyRound, Plus } from "lucide-react";
import { useId, useState, type ReactNode } from "react";

import { Callout } from "@/components/callout";
import { EmptyState } from "@/components/empty-state";
import { ErrorState } from "@/components/error-state";
import { ReadOnlyNote } from "@/components/read-only-note";
import { SectionCard } from "@/components/section-card";
import { TileListSkeleton } from "@/components/tile-list";
import { Button } from "@/components/ui/button";
import { useMe } from "@/features/auth";
import { useCurrentOrg, usePermission } from "@/features/shell";
import type { Route } from "@/lib/api";
import { viewOnlyCopy } from "@/lib/permissions";

import { useGatewayRoutesQuery } from "../gateway-queries";
import { GatewayLayout } from "../gateway-layout";
import { AddCredentialDialog } from "./add-credential-dialog";
import { CredentialList } from "./credential-list";
import { NOT_CONFIGURED_COPY } from "./credential-model";
import { useCredentialsQuery } from "./credentials-queries";

/**
 * Figma "Gateway — Credentials" and its states. Org-level: every member reads the list, only
 * owners add, check, rotate or delete. A server without `CREDENTIALS_KEYS` says so in `/me`, so the
 * notice shows up front and Add is off; a `409 NOT_CONFIGURED` answer (the setting removed since
 * `/me` loaded) does the same.
 */
export function GatewayCredentialsPage() {
  const org = useCurrentOrg();
  const orgName = org?.name ?? null;
  const canManage = usePermission("credentials:manage");
  const credentialsQuery = useCredentialsQuery();
  // Best effort: it only adds "Used by route" hints, so a failure here never blocks the list.
  const routes = useGatewayRoutesQuery().data ?? [];
  const serverNotConfigured = !useMe().capabilities.credentials_configured;
  const [refused, setRefused] = useState(false);
  const notConfigured = serverNotConfigured || refused;
  const notConfiguredId = useId();

  // `aria-disabled`, not `disabled`: the button keeps focus when the dialog closes into the
  // not-configured state, and screen readers reach the reason it points at.
  const addAction = canManage ? (
    <AddCredentialDialog
      disabled={notConfigured}
      orgName={orgName}
      onNotConfigured={() => {
        setRefused(true);
      }}
      trigger={
        <Button
          variant="primary"
          aria-disabled={notConfigured || undefined}
          aria-describedby={notConfigured ? notConfiguredId : undefined}
          className="aria-disabled:cursor-not-allowed aria-disabled:opacity-50"
        >
          <Plus aria-hidden />
          Add credential
        </Button>
      }
    />
  ) : null;

  return (
    <GatewayLayout>
      <SectionCard
        title="Provider credentials"
        description={
          <>
            Shared by every project in {orgName ?? "your organization"}. Keys are encrypted at rest
            and never shown again.
          </>
        }
        actions={addAction}
      >
        {canManage ? null : (
          <ReadOnlyNote>{viewOnlyCopy("provider credentials", "credentials:manage")}</ReadOnlyNote>
        )}
        {notConfigured ? (
          <div id={notConfiguredId}>
            <Callout
              tone="warning"
              role={refused ? "alert" : undefined}
              title="Not configured on this server"
            >
              {NOT_CONFIGURED_COPY}
            </Callout>
          </div>
        ) : null}
        <CredentialsContent
          query={credentialsQuery}
          routes={routes}
          canManage={canManage}
          notConfigured={notConfigured}
          addAction={addAction}
          onNotConfigured={() => {
            setRefused(true);
          }}
        />
        <p className="px-1 text-xs font-medium text-subtle-foreground">
          Checking calls the provider&rsquo;s model list with the stored key. Deleting is blocked
          while a route targets the credential.
        </p>
      </SectionCard>
    </GatewayLayout>
  );
}

interface CredentialsContentProps {
  query: ReturnType<typeof useCredentialsQuery>;
  routes: readonly Route[];
  canManage: boolean;
  notConfigured: boolean;
  addAction: ReactNode;
  onNotConfigured: () => void;
}

function CredentialsContent({
  query,
  routes,
  canManage,
  notConfigured,
  addAction,
  onNotConfigured,
}: CredentialsContentProps) {
  if (query.isPending) {
    return <TileListSkeleton label="Loading credentials" />;
  }

  if (query.isError) {
    return (
      <ErrorState
        compact
        error={query.error}
        title="Couldn't load credentials"
        onRetry={() => {
          void query.refetch();
        }}
      />
    );
  }

  if (query.data.length === 0) {
    return (
      <EmptyState
        icon={KeyRound}
        title="No provider credentials"
        description={
          notConfigured
            ? "Once the server key is set, add an OpenAI, Anthropic or OpenAI-compatible key here."
            : "Add an OpenAI, Anthropic or OpenAI-compatible key so routes can call providers."
        }
        action={addAction ?? undefined}
      />
    );
  }

  return (
    <CredentialList
      credentials={query.data}
      routes={routes}
      canManage={canManage}
      onNotConfigured={onNotConfigured}
    />
  );
}
