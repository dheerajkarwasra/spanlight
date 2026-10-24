import { Link } from "@tanstack/react-router";
import { Controller, type Control } from "react-hook-form";

import { ChipMultiSelect } from "@/components/chip-multi-select";
import { FormField } from "@/components/form-field";
import { Button } from "@/components/ui/button";
import { channelOptions, MAX_RULE_CHANNELS, useAlertChannelsQuery } from "@/features/alerts";
import { useProjectParams } from "@/features/shell";

import type { ProjectSettingsValues } from "./project-settings-schema";

export const INSIGHT_CHANNELS_HINT = "Only critical insights notify, once each time one opens.";

interface InsightChannelsFieldProps {
  control: Control<ProjectSettingsValues>;
  canEdit: boolean;
}

/**
 * Figma "Insight notifications" channels: the org's alert channels as a chip picker. Members
 * and viewers see the saved choice and why they can't change it.
 */
export function InsightChannelsField({ control, canEdit }: InsightChannelsFieldProps) {
  const query = useAlertChannelsQuery();
  const channels = query.data ?? [];

  return (
    <Controller
      control={control}
      name="insight_channel_ids"
      render={({ field, fieldState }) => {
        const options = channelOptions(channels, field.value, []);
        if (!canEdit) {
          return (
            <ReadOnlyChannels
              names={field.value.map(
                (id) => options.find((option) => option.value === id)?.label ?? "Deleted channel",
              )}
              status={queryStatus(query)}
            />
          );
        }
        // Saved ids are not in the options until the channels are known: say so instead of
        // drawing them as deleted channels.
        if (query.isPending || query.isError) {
          return (
            <ChannelsUnavailable failed={query.isError} onRetry={() => void query.refetch()} />
          );
        }
        if (channels.length === 0 && field.value.length === 0) {
          return <NoChannelsYet />;
        }
        return (
          <FormField
            label="Channels"
            hint={INSIGHT_CHANNELS_HINT}
            error={fieldState.error?.message}
          >
            <ChipMultiSelect
              value={field.value}
              onChange={field.onChange}
              options={options}
              placeholder="Add a channel…"
              label="Channels"
              max={MAX_RULE_CHANNELS}
            />
          </FormField>
        );
      }}
    />
  );
}

type ChannelsStatus = "pending" | "error" | "ready";

function queryStatus(query: { isPending: boolean; isError: boolean }): ChannelsStatus {
  if (query.isPending) {
    return "pending";
  }
  return query.isError ? "error" : "ready";
}

function ReadOnlyChannels({ names, status }: { names: readonly string[]; status: ChannelsStatus }) {
  let content = <span className="text-muted-foreground">No channels</span>;
  if (status === "pending") {
    content = <span className="text-muted-foreground">Loading channels…</span>;
  } else if (status === "error") {
    content = <span className="text-muted-foreground">Channel names unavailable</span>;
  } else if (names.length > 0) {
    content = (
      <ul aria-label="Channels" className="flex flex-wrap gap-2">
        {names.map((name, index) => (
          <li
            key={`${name}-${index}`}
            className="rounded-full bg-surface px-2.5 py-1 text-xs font-medium text-foreground"
          >
            {name}
          </li>
        ))}
      </ul>
    );
  }
  return (
    <div className="flex flex-col gap-2">
      <span className="text-sm font-medium text-foreground">Channels</span>
      <div className="min-h-11 rounded-input border border-border bg-surface-muted px-3 py-2 text-sm">
        {content}
      </div>
      <div className="flex flex-col gap-0.5">
        <p className="text-xs font-medium text-muted-foreground">{INSIGHT_CHANNELS_HINT}</p>
      </div>
    </div>
  );
}

function ChannelsUnavailable({ failed, onRetry }: { failed: boolean; onRetry: () => void }) {
  return (
    <div className="flex flex-col gap-2">
      <span className="text-sm font-medium text-foreground">Channels</span>
      <div className="flex min-h-11 items-center justify-between gap-3 rounded-input border border-border bg-surface-muted px-3 py-2 text-sm text-muted-foreground">
        {failed
          ? "Couldn't load channels. Check your connection and try again."
          : "Loading channels…"}
        {failed ? (
          <Button size="sm" className="shrink-0" onClick={onRetry}>
            Try again
          </Button>
        ) : null}
      </div>
    </div>
  );
}

/** No channels in the org yet: the way to add one. */
function NoChannelsYet() {
  const { orgId, projectId } = useProjectParams();
  return (
    <p className="text-sm text-muted-foreground">
      No channels yet. Add one under{" "}
      <Link
        to="/$orgId/$projectId/settings/channels"
        params={{ orgId, projectId }}
        className="rounded-xs font-semibold text-accent underline-offset-4 hover:underline"
      >
        Settings › Notification channels
      </Link>
      , then pick it here.
    </p>
  );
}
