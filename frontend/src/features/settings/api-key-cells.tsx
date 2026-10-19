import { toast } from "sonner";

import { ConfirmDialog } from "@/components/confirm-dialog";
import { RelativeTime } from "@/components/relative-time";
import { RowAction } from "@/components/tile-list";
import { Badge } from "@/components/ui/badge";
import { UnknownValue } from "@/components/unknown-value";
import type { ApiKey } from "@/lib/api";
import { formatDate } from "@/lib/format";

import { useRevokeApiKey } from "./api-key-queries";
import { revokeDecision, type KeyStatus, type RevokeAbility } from "./api-key-utils";
import { REVOKE_DESCRIPTION } from "./revoke-copy";

/** The cells of one key row that are more than a line of text. */

export function LastUsed({ apiKey }: { apiKey: ApiKey }) {
  if (apiKey.last_used_at === null) {
    return <span className="text-subtle-foreground">Never</span>;
  }
  return <RelativeTime iso={apiKey.last_used_at} />;
}

/** The creation date, and under it who made the key (Figma "by {name}"). */
export function CreatedCell({ apiKey }: { apiKey: ApiKey }) {
  const creator = apiKey.created_by;
  return (
    <>
      <span className="sr-only">Created </span>
      <time dateTime={apiKey.created_at} className="block truncate text-sm">
        {formatDate(apiKey.created_at)}
      </time>
      <span className="block truncate text-xs font-medium text-subtle-foreground">
        by{" "}
        {creator === null ? (
          <UnknownValue reason="The account that created this key no longer exists" />
        ) : (
          <span title={creator.email}>{creator.name || creator.email}</span>
        )}
      </span>
    </>
  );
}

export function KeyStatusBadge({
  apiKey,
  status,
  size,
}: {
  apiKey: ApiKey;
  status: KeyStatus;
  size?: "sm" | "md";
}) {
  if (status === "revoked") {
    return (
      <Badge size={size}>
        Revoked
        <span className="sr-only"> on {formatDate(apiKey.revoked_at)}</span>
      </Badge>
    );
  }
  if (status === "expired") {
    return (
      <Badge variant="warning" size={size}>
        Expired
      </Badge>
    );
  }
  return (
    <Badge variant="success" size={size}>
      Active
    </Badge>
  );
}

export function RevokeKeyAction({ apiKey, ability }: { apiKey: ApiKey; ability: RevokeAbility }) {
  const revokeKey = useRevokeApiKey();
  const decision = revokeDecision(apiKey, ability);

  // A key this role may not revoke shows no action at all (the server refuses it anyway).
  if (!decision.allowed) {
    return null;
  }

  return (
    <ConfirmDialog
      trigger={<RowAction aria-label={`Revoke key ${apiKey.name}`}>Revoke</RowAction>}
      title={`Revoke ${apiKey.name}?`}
      description={REVOKE_DESCRIPTION}
      confirmLabel="Revoke"
      onConfirm={async () => {
        await revokeKey.mutateAsync(apiKey.id);
        toast.success(`Revoked API key "${apiKey.name}".`);
      }}
    />
  );
}
