import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";

import { useAuditIntegrityQuery } from "./audit-stream-queries";

const HEAD_CHARS = 16;

/**
 * Figma "Audit log v3" Integrity: the org's hash chain, recomputed on the server. Every event is
 * hashed with the one before it, so a changed or removed event shows as a break here.
 */
export function AuditIntegrity() {
  const query = useAuditIntegrityQuery();
  if (query.isPending) {
    return <Skeleton className="h-11 w-full rounded-[10px]" />;
  }
  if (query.isError) {
    return null;
  }
  const check = query.data;
  if (check.checked === 0) {
    return null;
  }
  return (
    <div
      role="status"
      className="flex flex-wrap items-center gap-x-2 gap-y-1 rounded-[10px] bg-surface-muted px-3 py-2.5 text-xs font-medium text-muted-foreground"
    >
      {check.ok ? (
        <Badge variant="success" size="sm">
          Chain intact
        </Badge>
      ) : (
        <Badge variant="danger" size="sm">
          Chain broken
        </Badge>
      )}
      {check.ok ? (
        <span>
          {check.checked.toLocaleString()} events checked · newest{" "}
          <span title={check.head_hash ?? undefined} className="font-mono text-foreground">
            {check.head_hash ? `${check.head_hash.slice(0, HEAD_CHARS)}…` : "—"}
          </span>
        </span>
      ) : (
        <span className="text-danger-text">
          Event #{check.first_broken_seq} no longer matches its hash: it or the one before it was
          changed or removed.
        </span>
      )}
      <span className="basis-full text-muted-foreground lg:ml-auto lg:basis-auto">
        Every event is hashed with the one before it, so a changed or removed event shows here.
      </span>
    </div>
  );
}
