import { RowAction } from "@/components/tile-list";
import type { GatewayKey } from "@/lib/api";

import { EditKeySheet } from "./edit-key-sheet";
import { RevokeKeyAction } from "./revoke-key-action";

/** Edit and Revoke for one row; nothing for a role that can't change keys. */
export function KeyRowActions({
  gatewayKey,
  canWrite,
}: {
  gatewayKey: GatewayKey;
  canWrite: boolean;
}) {
  if (!canWrite) {
    return null;
  }
  return (
    <span className="flex gap-1.5">
      <EditKeySheet
        gatewayKey={gatewayKey}
        trigger={<RowAction aria-label={`Edit key ${gatewayKey.name}`}>Edit</RowAction>}
      />
      <RevokeKeyAction gatewayKey={gatewayKey} variant="row" />
    </span>
  );
}
