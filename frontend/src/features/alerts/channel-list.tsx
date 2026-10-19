import { useId } from "react";

import { Badge } from "@/components/ui/badge";
import type { AlertChannel, AlertChannelKind } from "@/lib/api";

import { groupByKind, KIND_ICONS, KIND_LABELS } from "./channel-kinds";
import { ChannelRow } from "./channel-row";

interface ChannelListProps {
  channels: readonly AlertChannel[];
  canWrite: boolean;
}

/** Figma "Alerts — Channels": the channels grouped by kind under "Alerts/Group heading". */
export function ChannelList({ channels, canWrite }: ChannelListProps) {
  return (
    <div className="@container flex flex-col gap-4">
      {groupByKind(channels).map((group) => (
        <ChannelGroup
          key={group.kind}
          kind={group.kind}
          channels={group.channels}
          canWrite={canWrite}
        />
      ))}
    </div>
  );
}

interface ChannelGroupProps extends ChannelListProps {
  kind: AlertChannelKind;
}

function ChannelGroup({ kind, channels, canWrite }: ChannelGroupProps) {
  const headingId = useId();
  const Icon = KIND_ICONS[kind];
  return (
    <section aria-labelledby={headingId} className="flex flex-col gap-1.5">
      <div className="flex items-center gap-2 px-1 pt-1.5 pb-0.5">
        <Icon aria-hidden className="size-4 text-foreground" strokeWidth={1.75} />
        <h3 id={headingId} className="text-sm font-bold text-foreground">
          {KIND_LABELS[kind]}
          <span className="sr-only">
            , {channels.length === 1 ? "1 channel" : `${channels.length} channels`}
          </span>
        </h3>
        <Badge aria-hidden>{channels.length}</Badge>
      </div>
      <ul aria-labelledby={headingId} className="flex flex-col gap-1.5">
        {channels.map((channel) => (
          <ChannelRow key={channel.id} channel={channel} canWrite={canWrite} />
        ))}
      </ul>
    </section>
  );
}
