import { ChipMultiSelect, type ChipOption } from "@/components/chip-multi-select";
import { FormField } from "@/components/form-field";
import { KIND_ICONS, KIND_LABELS } from "@/features/alerts";
import type { AlertChannel } from "@/lib/api";

import { MAX_BUDGET_CHANNELS } from "./budget-schema";
import { useAlertChannelsQuery } from "./budgets-queries";

function channelOption(channel: AlertChannel): ChipOption {
  const Icon = KIND_ICONS[channel.kind];
  return {
    value: channel.id,
    label: channel.name,
    detail: KIND_LABELS[channel.kind],
    leading: (
      <span
        aria-hidden
        className="flex size-7 shrink-0 items-center justify-center rounded-full border border-border bg-surface"
      >
        <Icon className="size-3.5 text-foreground" strokeWidth={1.75} />
      </span>
    ),
  };
}

interface BudgetChannelsFieldProps {
  value: string[];
  onChange: (value: string[]) => void;
  error: string | undefined;
}

/**
 * Figma budget dialog "Channels": where the budget's alerts go, picked from the organization's
 * channels, at most 10. A channel deleted since the budget was saved shows as an error chip.
 */
export function BudgetChannelsField({ value, onChange, error }: BudgetChannelsFieldProps) {
  const channels = useAlertChannelsQuery();
  const known = (channels.data ?? []).map(channelOption);
  const knownIds = new Set(known.map((option) => option.value));
  const missing: ChipOption[] = value
    .filter((id) => !knownIds.has(id))
    .map((id) => ({
      value: id,
      label: channels.isPending ? "Loading…" : "Deleted channel",
      invalid: channels.isSuccess,
    }));
  const empty = channels.isSuccess && known.length === 0;
  const loadProblem = channels.isError
    ? "Couldn't load the channels. Close and try again."
    : undefined;

  return (
    <FormField
      label="Channels"
      hint={
        empty
          ? "No channels yet. Add one under Settings › Notification channels to get budget alerts."
          : `Where budget alerts go. Pick up to ${MAX_BUDGET_CHANNELS}.`
      }
      error={error ?? loadProblem}
    >
      <ChipMultiSelect
        value={value}
        onChange={onChange}
        options={[...known, ...missing]}
        placeholder={channels.isPending ? "Loading channels…" : "Add a channel"}
        label="Channels"
        max={MAX_BUDGET_CHANNELS}
      />
    </FormField>
  );
}
