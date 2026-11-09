import { KeyRound } from "lucide-react";
import { useState, type ReactNode } from "react";

import { EmptyState } from "@/components/empty-state";
import { ErrorState } from "@/components/error-state";
import { SectionCard } from "@/components/section-card";
import { Button } from "@/components/ui/button";

import { CreateTokenDialog } from "./create-token-dialog";
import { TokenList, TokenListSkeleton } from "./token-list";
import { useTokensQuery } from "./token-queries";

/**
 * Settings › Tokens: the personal access tokens a person has made (Figma "Settings — Tokens").
 * The server lists only usable ones, so a revoked or expired token leaves the list.
 */
export function TokensPage() {
  const query = useTokensQuery();
  const empty = query.isSuccess && query.data.length === 0;
  const [creating, setCreating] = useState(false);
  const startCreating = () => {
    setCreating(true);
  };

  return (
    <>
      <SectionCard
        title="Personal access tokens"
        description="Tokens let scripts and tools call the Spanlight API as you. A token is shown once, when it's created."
        // The empty state carries the one call to action, so the header button steps aside.
        actions={
          empty ? undefined : (
            <CreateTokenButton disabled={query.isPending} onClick={startCreating} />
          )
        }
        className="gap-3"
      >
        <TokensContent query={query} onCreate={startCreating} />
      </SectionCard>
      {/* Here, not beside a button: the refresh after creating removes the empty state. */}
      <CreateTokenDialog open={creating} onOpenChange={setCreating} />
    </>
  );
}

function TokensContent({
  query,
  onCreate,
}: {
  query: ReturnType<typeof useTokensQuery>;
  onCreate: () => void;
}) {
  if (query.isPending) {
    return <TokenListSkeleton />;
  }

  if (query.isError) {
    return (
      <Tile>
        <ErrorState
          compact
          error={query.error}
          title="Couldn't load your tokens"
          onRetry={() => {
            void query.refetch();
          }}
        />
      </Tile>
    );
  }

  if (query.data.length === 0) {
    return (
      <Tile>
        <EmptyState
          icon={KeyRound}
          title="No tokens yet"
          description="Create a token to call the Spanlight API from scripts, CI jobs or notebooks."
          action={<CreateTokenButton onClick={onCreate} />}
        />
      </Tile>
    );
  }

  return (
    <>
      <TokenList tokens={query.data} />
      <p className="flex items-start gap-2 px-1 pt-1.5 text-xs font-medium text-subtle-foreground">
        <KeyRound aria-hidden className="mt-px size-3.5 shrink-0" />
        <span>
          Tokens act with your current role in each organization. Expired and revoked tokens stop
          working and leave this list.
        </span>
      </p>
    </>
  );
}

/** The muted tile the empty and error states sit on. */
function Tile({ children }: { children: ReactNode }) {
  return <div className="rounded-tile bg-surface-muted">{children}</div>;
}

function CreateTokenButton({
  disabled = false,
  onClick,
}: {
  disabled?: boolean;
  onClick: () => void;
}) {
  return (
    <Button variant="primary" disabled={disabled} onClick={onClick}>
      Create token
    </Button>
  );
}
