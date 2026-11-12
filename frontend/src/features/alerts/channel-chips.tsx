import { CircleSlash, type LucideIcon } from "lucide-react";
import type { ReactNode } from "react";

import { Tooltip } from "@/components/ui/tooltip";
import type { AlertChannel } from "@/lib/api";
import { cn } from "@/lib/utils";

import { KIND_ICONS } from "./channel-kinds";

const CHIP_CLASSES =
  "inline-flex h-6 max-w-full min-w-0 items-center gap-[5px] rounded-full border border-border bg-surface py-[3px] pr-[9px] pl-[7px] text-xs leading-[1.4] font-medium text-foreground";

function Chip({ icon: Icon, children }: { icon?: LucideIcon; children: ReactNode }) {
  return (
    <li className={CHIP_CLASSES}>
      {Icon ? <Icon aria-hidden className="size-3 shrink-0" strokeWidth={2} /> : null}
      <span className="truncate">{children}</span>
    </li>
  );
}

interface ChannelChipsProps {
  channelIds: readonly string[];
  /** The org's channels by id; undefined while they load or when they failed to load. */
  channels: ReadonlyMap<string, AlertChannel> | undefined;
  /** Show at most this many chips, then "+N". */
  max?: number;
  className?: string;
}

/**
 * A rule's channels as chips with their kind icon. A channel deleted since the rule was saved
 * shows as "Deleted channel": the evaluator skips it until the rule is edited.
 */
export function ChannelChips({ channelIds, channels, max, className }: ChannelChipsProps) {
  if (channelIds.length === 0) {
    return <span className="text-xs font-medium text-muted-foreground">No channels</span>;
  }
  if (channels === undefined) {
    return (
      <span className="text-xs font-medium text-muted-foreground">
        {channelIds.length === 1 ? "1 channel" : `${channelIds.length} channels`}
      </span>
    );
  }
  const shown = max === undefined ? channelIds : channelIds.slice(0, max);
  const hidden = channelIds.length - shown.length;

  return (
    <ul aria-label="Channels" className={cn("flex min-w-0 flex-wrap gap-1.5", className)}>
      {shown.map((id) => {
        const channel = channels.get(id);
        return channel ? (
          <Chip key={id} icon={KIND_ICONS[channel.kind]}>
            {channel.name}
          </Chip>
        ) : (
          <DeletedChip key={id} />
        );
      })}
      {hidden > 0 ? (
        <Chip>
          <span aria-hidden>+{hidden}</span>
          <span className="sr-only">and {hidden} more</span>
        </Chip>
      ) : null}
    </ul>
  );
}

function DeletedChip() {
  return (
    <Tooltip content="This channel was deleted. The rule skips it until it is edited.">
      <li
        tabIndex={0}
        className={cn(CHIP_CLASSES, "relative z-10 border-dashed text-muted-foreground")}
      >
        <CircleSlash aria-hidden className="size-3 shrink-0" strokeWidth={2} />
        Deleted channel
      </li>
    </Tooltip>
  );
}
