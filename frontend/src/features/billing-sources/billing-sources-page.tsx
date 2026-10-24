import { Link } from "@tanstack/react-router";
import { ExternalLink, Receipt } from "lucide-react";
import { useState, type ReactNode } from "react";

import { Callout } from "@/components/callout";
import { EmptyState } from "@/components/empty-state";
import { ErrorState } from "@/components/error-state";
import { ReadOnlyNote } from "@/components/read-only-note";
import { SectionCard } from "@/components/section-card";
import { TileListSkeleton } from "@/components/tile-list";
import { Button } from "@/components/ui/button";
import { useCurrentOrg, usePermission, useProjectParams } from "@/features/shell";
import { docsLink } from "@/lib/docs-links";
import { viewOnlyCopy } from "@/lib/permissions";

import { useBillingSourcesQuery } from "./billing-sources-queries";
import { CreateSourceDialog } from "./create-source-dialog";
import { SourceList } from "./source-list";

/**
 * Figma "Billing sources / list" and its states (empty, not configured, loading, error, mobile).
 * Every member reads the list; admins and owners add, refresh, upload, edit and delete. The page
 * learns the server has no CREDENTIALS_KEYS only from a `409 NOT_CONFIGURED`, then shows the
 * notice and offers the CSV kind alone.
 */
export function BillingSourcesPage() {
  const org = useCurrentOrg();
  const canManage = usePermission("billing_sources:manage");
  const params = useProjectParams();
  const query = useBillingSourcesQuery();
  // The server's `409 NOT_CONFIGURED` notice, once it answered one; null until then.
  const [notConfiguredCopy, setNotConfiguredCopy] = useState<string | null>(null);
  const notConfigured = notConfiguredCopy !== null;

  const addAction = (
    <CreateSourceDialog
      apiKindsOff={notConfigured}
      onNotConfigured={setNotConfiguredCopy}
      trigger={<Button variant="primary">Add source</Button>}
    />
  );

  return (
    <div className="flex flex-col gap-5">
      {canManage ? null : (
        <ReadOnlyNote>{viewOnlyCopy("billing sources", "billing_sources:manage")}</ReadOnlyNote>
      )}
      <SectionCard
        title="Billing sources"
        description={`What Anthropic and OpenAI bill ${org?.name ?? "your organization"}, fetched daily and compared with Spanlight’s own figures per project. Admin keys are encrypted at rest and never shown again.`}
        actions={
          <>
            <Button variant="secondary" asChild>
              <Link to="/$orgId/$projectId/reconciliation" params={params}>
                View reconciliation
              </Link>
            </Button>
            {canManage ? addAction : null}
          </>
        }
        className="gap-3"
      >
        {notConfigured ? (
          <Callout tone="warning" role="alert">
            {notConfiguredCopy}
          </Callout>
        ) : null}
        <SourcesContent
          query={query}
          canManage={canManage}
          notConfigured={notConfigured}
          addAction={addAction}
        />
      </SectionCard>
    </div>
  );
}

interface SourcesContentProps {
  query: ReturnType<typeof useBillingSourcesQuery>;
  canManage: boolean;
  notConfigured: boolean;
  addAction: ReactNode;
}

function SourcesContent({ query, canManage, notConfigured, addAction }: SourcesContentProps) {
  if (query.isPending) {
    return <TileListSkeleton label="Loading billing sources" rows={3} className="h-[76px]" />;
  }

  if (query.isError) {
    return (
      <ErrorState
        compact
        error={query.error}
        title="Couldn't load billing sources"
        retrying={query.isFetching}
        onRetry={() => {
          void query.refetch();
        }}
      />
    );
  }

  if (query.data.length === 0) {
    return (
      <div className="flex flex-col gap-3">
        <EmptyState
          icon={Receipt}
          title="No billing sources"
          className="rounded-tile bg-surface-muted"
          description={
            notConfigured
              ? "Upload a provider cost CSV to compare it with Spanlight’s figures. API sources become available once CREDENTIALS_KEYS is set."
              : "Connect an Anthropic or OpenAI Admin API key to fetch the provider’s daily cost report, or upload the provider’s cost CSV. Spanlight compares both with what it computed per span."
          }
          action={canManage ? addAction : undefined}
        />
        <Button variant="link" asChild className="self-start px-1">
          <a href={docsLink("guides/reconciliation/")} target="_blank" rel="noreferrer">
            How reconciliation works
            <ExternalLink aria-hidden />
            <span className="sr-only">(opens in a new tab)</span>
          </a>
        </Button>
      </div>
    );
  }

  return <SourceList sources={query.data} canManage={canManage} />;
}
