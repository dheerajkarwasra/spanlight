import { useNavigate } from "@tanstack/react-router";

import { StatusDot } from "@/components/status-dot";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

import { DisabledFilter } from "./disabled-filter";
import { useFilterOptionsQuery, useProjectFilters } from "./project-context";

const ALL = "__all__";

/**
 * The environment pill (Figma "Environment pill"): filters project data by environment. With a
 * `disabledReason` the page can't honour it: the pill is disabled and the reason is shown.
 */
export function EnvironmentFilter({ disabledReason }: { disabledReason?: string }) {
  const navigate = useNavigate();
  const { environment } = useProjectFilters();
  const optionsQuery = useFilterOptionsQuery();
  const environments = optionsQuery.data?.environments ?? [];
  const options =
    environment && !environments.includes(environment)
      ? [environment, ...environments]
      : environments;

  const pill = (
    <Select
      disabled={disabledReason !== undefined}
      value={environment ?? ALL}
      onValueChange={(value) => {
        void navigate({
          to: ".",
          search: (prev) => ({ ...prev, env: value === ALL ? undefined : value }),
        });
      }}
    >
      <SelectTrigger
        className="h-[43px] w-auto max-w-48 min-w-0 gap-2 rounded-full border-border pr-3 pl-3.5 font-medium shadow-card"
        aria-label="Environment"
      >
        <StatusDot state={environment ? "ok" : "unset"} />
        <SelectValue />
      </SelectTrigger>
      <SelectContent align="end">
        <SelectItem value={ALL}>All environments</SelectItem>
        {options.map((name) => (
          <SelectItem key={name} value={name}>
            {name}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  );
  return disabledReason === undefined ? (
    pill
  ) : (
    <DisabledFilter label="Environment" reason={disabledReason}>
      {pill}
    </DisabledFilter>
  );
}
