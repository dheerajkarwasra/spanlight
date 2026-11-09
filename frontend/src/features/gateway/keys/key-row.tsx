import { ColumnLabels } from "@/components/tile-list";
import { Badge } from "@/components/ui/badge";
import { formatDate } from "@/lib/format";
import type { GatewayKey } from "@/lib/api";
import { cn } from "@/lib/utils";

import { EnvironmentBadge } from "../environment-badge";
import { KeyRowActions } from "./key-actions";
import { KEY_COLUMNS } from "./key-columns";
import { CacheCell, FaultTag, LastUsed, LimitsCell, RouteCell } from "./key-cells";

export function KeyColumnLabels() {
  return (
    <ColumnLabels className="gap-2 pr-3 pl-4 @[52rem]:flex">
      <span className={KEY_COLUMNS.name}>Key</span>
      <span className={KEY_COLUMNS.environment}>Environment</span>
      <span className={KEY_COLUMNS.route}>Route</span>
      <span className={KEY_COLUMNS.limits}>Limits</span>
      <span className={KEY_COLUMNS.cache}>Cache</span>
      <span className={KEY_COLUMNS.lastUsed}>Last used</span>
      <span className="w-[132px] shrink-0" />
    </ColumnLabels>
  );
}

interface KeyRowProps {
  gatewayKey: GatewayKey;
  canWrite: boolean;
  routeNames: ReadonlyMap<string, string> | undefined;
  /** Fault profile names by id; undefined while they load. */
  profileNames: ReadonlyMap<string, string> | undefined;
}

export function KeyRow({ gatewayKey, canWrite, routeNames, profileNames }: KeyRowProps) {
  const revoked = gatewayKey.revoked_at !== null;
  // No creator means a demo key or a deleted account: nobody known has left.
  const creatorLeft = !revoked && gatewayKey.created_by !== null && !gatewayKey.creator_is_member;
  const profileId = gatewayKey.fault_profile_id;
  const profileName = profileId === null ? null : (profileNames?.get(profileId) ?? "Fault profile");

  return (
    <li
      className={cn(
        "flex flex-wrap items-center gap-x-2 gap-y-2 rounded-tile",
        revoked
          ? "border border-dashed border-border py-[9px] pr-[11px] pl-[15px] text-subtle-foreground"
          : "bg-surface-muted py-2.5 pr-3 pl-4 text-muted-foreground",
      )}
    >
      <div className={KEY_COLUMNS.name}>
        <span
          title={gatewayKey.name}
          className={cn(
            "block truncate text-sm font-semibold",
            revoked ? "text-subtle-foreground" : "text-foreground",
          )}
        >
          {gatewayKey.name}
        </span>
        <span title={`${gatewayKey.prefix}…`} className="block truncate font-mono text-label">
          {gatewayKey.prefix}…
        </span>
        {profileName || creatorLeft ? (
          <span className="mt-1 flex flex-wrap gap-1">
            {profileName ? <FaultTag name={profileName} /> : null}
            {creatorLeft ? (
              <Badge variant="warning" size="sm">
                Creator left<span className="sr-only">: created by someone no longer a member</span>
              </Badge>
            ) : null}
          </span>
        ) : null}
      </div>
      <div className={KEY_COLUMNS.environment}>
        <span className="sr-only">Environment </span>
        <EnvironmentBadge environment={gatewayKey.environment} />
      </div>
      <div className={cn(KEY_COLUMNS.route, "text-sm")}>
        <span className="sr-only">Route </span>
        <RouteCell routeId={gatewayKey.route_id} routeNames={routeNames} />
      </div>
      <div className={cn(KEY_COLUMNS.limits, "text-sm")}>
        <span className="sr-only">Limits </span>
        <LimitsCell gatewayKey={gatewayKey} />
      </div>
      <div className={cn(KEY_COLUMNS.cache, "text-sm")}>
        <span className="sr-only">Cache </span>
        <CacheCell gatewayKey={gatewayKey} />
      </div>
      <div className={cn(KEY_COLUMNS.lastUsed, "text-sm")}>
        <span className="sr-only">Last used </span>
        <LastUsed gatewayKey={gatewayKey} />
      </div>
      <div className={KEY_COLUMNS.action}>
        {revoked ? (
          <Badge>
            Revoked
            <span className="sr-only"> on {formatDate(gatewayKey.revoked_at)}</span>
          </Badge>
        ) : (
          <KeyRowActions gatewayKey={gatewayKey} canWrite={canWrite} />
        )}
      </div>
      {/* Narrow cards: the facts wrap under the name. */}
      <dl className="order-last flex w-full flex-wrap items-center gap-x-3 gap-y-1.5 text-xs font-medium @[52rem]:hidden">
        <div className="flex items-center gap-1.5">
          <dt className="sr-only">Environment</dt>
          <dd>
            <EnvironmentBadge environment={gatewayKey.environment} />
          </dd>
        </div>
        <div className="flex items-center gap-1">
          <dt>Route</dt>
          <dd className="min-w-0">
            <RouteCell routeId={gatewayKey.route_id} routeNames={routeNames} />
          </dd>
        </div>
        <div className="flex items-center gap-1">
          <dt className="sr-only">Limits</dt>
          <dd>
            <NarrowLimits gatewayKey={gatewayKey} />
          </dd>
        </div>
        <div className="flex items-center gap-1">
          <dt>Cache</dt>
          <dd>
            <CacheCell gatewayKey={gatewayKey} />
          </dd>
        </div>
        <div className="flex items-center gap-1">
          <dt>Last used</dt>
          <dd>
            <LastUsed gatewayKey={gatewayKey} />
          </dd>
        </div>
      </dl>
    </li>
  );
}

/** "No limits" when neither limit is set (the 375 px frame); otherwise both, rpm then tpm. */
function NarrowLimits({ gatewayKey }: { gatewayKey: GatewayKey }) {
  if (gatewayKey.rpm_limit === null && gatewayKey.tpm_limit === null) {
    return <span>No limits</span>;
  }
  return (
    <span className="flex flex-wrap gap-x-1.5">
      <LimitsCell gatewayKey={gatewayKey} />
    </span>
  );
}
