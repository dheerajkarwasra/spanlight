import { Info, Waypoints } from "lucide-react";
import { useState, type ReactNode } from "react";

import { EmptyState } from "@/components/empty-state";
import { ErrorState } from "@/components/error-state";
import { ReadOnlyNote } from "@/components/read-only-note";
import { SectionCard } from "@/components/section-card";
import { TileListSkeleton } from "@/components/tile-list";
import { Button } from "@/components/ui/button";
import { usePermission } from "@/features/shell";
import { viewOnlyCopy } from "@/lib/permissions";

import { GatewayLayout } from "../gateway-layout";
import { useGatewayRoutesQuery } from "../gateway-queries";
import { NewRouteDialog } from "./new-route-dialog";
import { NewRouteEditor } from "./route-editor-new";
import { RouteList } from "./route-list";
import { useRouteLookups } from "./use-route-lookups";

/**
 * Gateway › Routes. "Create route" asks for the name, then shows the editor on an unsaved route
 * in place of the list; the route exists once that editor saves it, and the page moves to it.
 */
export function GatewayRoutesPage() {
  const [draftName, setDraftName] = useState<string | null>(null);

  return (
    <GatewayLayout>
      {draftName === null ? (
        <RoutesCard onCreate={setDraftName} />
      ) : (
        <NewRouteEditor
          name={draftName}
          onCancel={() => {
            setDraftName(null);
          }}
        />
      )}
    </GatewayLayout>
  );
}

function RoutesCard({ onCreate }: { onCreate: (name: string) => void }) {
  const routesQuery = useGatewayRoutesQuery();
  const lookups = useRouteLookups();
  const canWrite = usePermission("gateway:write");
  const takenNames = routesQuery.data?.map((route) => route.name) ?? [];
  const createButton = canWrite ? (
    <NewRouteDialog
      takenNames={takenNames}
      onContinue={onCreate}
      trigger={<Button variant="primary">Create route</Button>}
    />
  ) : null;

  return (
    <SectionCard
      title="Routes"
      description="Routes decide which credential serves each call, and how failures retry and fall back. New keys use the default route."
      actions={routesQuery.isSuccess && routesQuery.data.length > 0 ? createButton : null}
      className="gap-3"
    >
      {canWrite ? null : (
        <ReadOnlyNote>{viewOnlyCopy("gateway routes", "gateway:write")}</ReadOnlyNote>
      )}
      <RoutesContent
        query={routesQuery}
        lookups={lookups}
        canWrite={canWrite}
        emptyAction={createButton}
      />
    </SectionCard>
  );
}

interface RoutesContentProps {
  query: ReturnType<typeof useGatewayRoutesQuery>;
  lookups: ReturnType<typeof useRouteLookups>;
  canWrite: boolean;
  emptyAction: ReactNode;
}

function RoutesContent({ query, lookups, canWrite, emptyAction }: RoutesContentProps) {
  if (query.isPending) {
    return <TileListSkeleton label="Loading routes" rows={3} className="h-16" />;
  }
  if (query.isError) {
    return (
      <ErrorState
        compact
        error={query.error}
        title="Couldn't load routes"
        onRetry={() => {
          void query.refetch();
        }}
      />
    );
  }
  if (query.data.length === 0) {
    return (
      <EmptyState
        icon={Waypoints}
        title="No routes"
        description="A route decides which provider credential serves a call, and what happens when it fails."
        action={emptyAction}
      />
    );
  }
  return (
    <>
      <RouteList routes={query.data} lookups={lookups} canWrite={canWrite} />
      <p className="flex items-start gap-2 px-1 pt-1.5 text-xs font-medium text-subtle-foreground">
        <Info aria-hidden className="mt-px size-3.5 shrink-0" />
        <span>
          Retries and fallbacks happen only before the first byte reaches your app. A route that
          keys use can&rsquo;t be deleted.
        </span>
      </p>
    </>
  );
}
