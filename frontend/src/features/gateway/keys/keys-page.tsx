import { EllipsisIcon, KeyRound, Trash2 } from "lucide-react";
import { useState, type ReactNode } from "react";

import { EmptyState } from "@/components/empty-state";
import { ErrorState } from "@/components/error-state";
import { ReadOnlyNote } from "@/components/read-only-note";
import { SectionCard } from "@/components/section-card";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { usePermission, useProjectQuery } from "@/features/shell";
import { viewOnlyCopy } from "@/lib/permissions";

import { GatewayLayout } from "../gateway-layout";
import {
  useFaultProfilesQuery,
  useGatewayKeysQuery,
  useGatewayRoutesQuery,
} from "../gateway-queries";
import { CreateKeyDialog } from "./create-key-dialog";
import { KeyList, KeyListSkeleton } from "./key-list";
import { PurgeCacheDialog } from "./purge-cache-dialog";

const KEYS_EMPTY_BODY =
  "A gateway key routes calls through Spanlight and sets limits for one app or environment.";

/** Figma "Gateway — Keys": the project's gateway keys, with create, edit, revoke and cache purge. */
export function GatewayKeysPage() {
  const keysQuery = useGatewayKeysQuery();
  const routesQuery = useGatewayRoutesQuery();
  const profilesQuery = useFaultProfilesQuery();
  const canWrite = usePermission("gateway:write");

  return (
    <GatewayLayout>
      <SectionCard
        title="Gateway keys"
        description="Each key routes calls for one app or environment. The secret is shown once."
        actions={<HeaderActions canWrite={canWrite} />}
        className="gap-3"
      >
        {canWrite ? null : (
          <ReadOnlyNote>{viewOnlyCopy("gateway keys", "gateway:write")}</ReadOnlyNote>
        )}
        <KeysContent
          query={keysQuery}
          canWrite={canWrite}
          routes={routesQuery.data}
          profiles={profilesQuery.data}
        />
      </SectionCard>
    </GatewayLayout>
  );
}

interface KeysContentProps {
  query: ReturnType<typeof useGatewayKeysQuery>;
  canWrite: boolean;
  routes: Parameters<typeof KeyList>[0]["routes"];
  profiles: Parameters<typeof KeyList>[0]["profiles"];
}

function KeysContent({ query, canWrite, routes, profiles }: KeysContentProps) {
  if (query.isPending) {
    return <KeyListSkeleton />;
  }

  if (query.isError) {
    return (
      <ErrorState
        compact
        error={query.error}
        title="Couldn't load gateway keys"
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
        title="No gateway keys"
        description={KEYS_EMPTY_BODY}
        action={canWrite ? <CreateKeyButton /> : undefined}
      />
    );
  }

  return (
    <>
      <KeyList keys={query.data} canWrite={canWrite} routes={routes} profiles={profiles} />
      <p className="px-1 pt-1.5 text-xs font-medium text-subtle-foreground">
        Apps send the key as <code className="font-mono">Authorization: Bearer</code> or{" "}
        <code className="font-mono">x-api-key</code>. A violet tag means a Lab fault profile is
        attached.
      </p>
    </>
  );
}

function HeaderActions({ canWrite }: { canWrite: boolean }): ReactNode {
  return canWrite ? (
    <>
      <CreateKeyButton />
      <KeysMenu />
    </>
  ) : null;
}

function CreateKeyButton() {
  return <CreateKeyDialog trigger={<Button variant="primary">Create key</Button>} />;
}

/** The header's overflow menu (Figma "Keys overflow"): Purge cache, for those who can write. */
function KeysMenu() {
  const [purgeOpen, setPurgeOpen] = useState(false);
  const project = useProjectQuery();

  return (
    <>
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <Button variant="secondary" size="icon" aria-label="More key actions">
            <EllipsisIcon aria-hidden />
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="end" className="min-w-64">
          <DropdownMenuItem
            className="h-auto items-start py-2"
            onSelect={() => {
              setPurgeOpen(true);
            }}
          >
            <Trash2 aria-hidden className="mt-0.5" />
            <span className="flex flex-col">
              <span className="font-medium">Purge cache</span>
              <span className="text-xs text-muted-foreground">
                Removes every cached response in {project.data?.name ?? "this project"}.
              </span>
            </span>
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>
      <PurgeCacheDialog
        open={purgeOpen}
        onOpenChange={setPurgeOpen}
        projectName={project.data?.name ?? "this project"}
      />
    </>
  );
}
