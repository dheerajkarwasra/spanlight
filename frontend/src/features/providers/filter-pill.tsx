import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

/** Radix selects cannot hold an empty value, so "All" has a stand-in. */
const ALL = "__all__";

export interface FilterOption<T extends string> {
  value: T;
  label: string;
}

interface FilterPillProps<T extends string> {
  /** "Provider": names the control and prefixes its value. */
  label: string;
  value: T | undefined;
  options: readonly FilterOption<T>[];
  onChange: (value: T | undefined) => void;
}

/** Figma "Provider: All" / "Class: All": a pill that names its filter and opens the choices. */
export function FilterPill<T extends string>({
  label,
  value,
  options,
  onChange,
}: FilterPillProps<T>) {
  return (
    <Select
      value={value ?? ALL}
      onValueChange={(next) => {
        onChange(options.find((option) => option.value === next)?.value);
      }}
    >
      <SelectTrigger
        aria-label={label}
        className="h-10 w-auto min-w-0 gap-1.5 rounded-full border-border px-[18px] font-semibold shadow-card"
      >
        <span className="flex min-w-0 items-center gap-1">
          <span>{label}:</span>
          <SelectValue />
        </span>
      </SelectTrigger>
      <SelectContent>
        <SelectItem value={ALL}>All</SelectItem>
        {options.map((option) => (
          <SelectItem key={option.value} value={option.value}>
            {option.label}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  );
}
