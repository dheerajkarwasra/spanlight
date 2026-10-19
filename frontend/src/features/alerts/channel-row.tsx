import { Badge } from "@/components/ui/badge";
import { Tooltip } from "@/components/ui/tooltip";
import type { AlertChannel } from "@/lib/api";
import { formatDate } from "@/lib/format";
import { cn } from "@/lib/utils";

import { channelTarget, KIND_ICONS, KIND_LABELS } from "./channel-kinds";
import { ChannelRowMenu } from "./channel-row-menu";
import { useTestChannel } from "./channels-queries";
import { DeliveryLogSheet } from "./delivery-log-sheet";
import { TestSendButton, TestSendResult } from "./test-send-button";

interface ChannelRowProps {
  channel: AlertChannel;
  canWrite: boolean;
}

/**
 * Figma "Alerts/Channel row" (kind tile, name, target, verified state, Test, Deliveries, more) and
 * its 375 px tile, which stacks the same parts. The last test's outcome shows under the row.
 */
export function ChannelRow({ channel, canWrite }: ChannelRowProps) {
  const test = useTestChannel(channel.id);
  const target = channelTarget(channel);
  const Icon = KIND_ICONS[channel.kind];

  return (
    <li className="flex flex-col gap-1.5">
      <div className="flex flex-col gap-3 rounded-tile bg-surface-muted p-4 @[48rem]:flex-row @[48rem]:items-center @[48rem]:py-2.5 @[48rem]:pr-3 @[48rem]:pl-4">
        <div className="flex items-start gap-3 @[48rem]:w-[270px] @[48rem]:shrink-0 @[48rem]:items-center">
          <span
            aria-hidden
            className="flex size-9 shrink-0 items-center justify-center rounded-tile border border-border bg-surface"
          >
            <Icon className="size-[18px] text-foreground" strokeWidth={1.75} />
          </span>
          <div className="flex min-w-0 flex-1 flex-col gap-0.5">
            <p className="truncate text-sm font-semibold text-foreground" title={channel.name}>
              {channel.name}
              <span className="sr-only">, {KIND_LABELS[channel.kind]}</span>
            </p>
            <p className="text-xs font-medium text-muted-foreground">
              Added {formatDate(channel.created_at) ?? "—"}
            </p>
          </div>
          <VerifiedBadge channel={channel} className="@[48rem]:hidden" />
        </div>
        <div className="flex min-w-0 flex-col gap-0.5 @[48rem]:flex-1">
          <p
            className={cn(
              "truncate text-foreground",
              target.mono ? "font-mono text-[13px]" : "text-sm",
            )}
          >
            {target.primary}
          </p>
          <p
            className="truncate text-xs font-medium text-muted-foreground"
            title={target.secondary}
          >
            {target.secondary}
          </p>
        </div>
        <div className="hidden w-[130px] shrink-0 @[48rem]:flex">
          <VerifiedBadge channel={channel} />
        </div>
        <div className="flex items-center gap-1.5 @[48rem]:justify-end @max-[48rem]:[&>button:not(:last-child)]:h-11 @max-[48rem]:[&>button:not(:last-child)]:flex-1">
          {canWrite ? <TestSendButton test={test} channelName={channel.name} /> : null}
          <DeliveryLogSheet channel={channel} canWrite={canWrite} />
          {canWrite ? <ChannelRowMenu channel={channel} /> : null}
        </div>
      </div>
      <TestSendResult test={test} />
    </li>
  );
}

function VerifiedBadge({ channel, className }: { channel: AlertChannel; className?: string }) {
  if (channel.verified_at === null) {
    return <Badge className={cn("bg-transparent px-0", className)}>Not tested yet</Badge>;
  }
  return (
    <Tooltip content={`Last test succeeded ${formatDate(channel.verified_at) ?? ""}`}>
      <Badge variant="success" tabIndex={0} className={className}>
        Verified
      </Badge>
    </Tooltip>
  );
}
