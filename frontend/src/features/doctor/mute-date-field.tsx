import { Button } from "@/components/ui/button";
import { DateInput } from "@/components/ui/input";
import { cn } from "@/lib/utils";

import { dateAfterDays, maxMuteDate, MUTE_PRESETS, toDateInput } from "./mute-form";

interface DateFieldProps {
  now: Date;
  value: string;
  onChange: (value: string) => void;
  id?: string;
  "aria-invalid"?: boolean;
  "aria-describedby"?: string;
}

/** The preset pills and the date input; the form field wires its id and error to the input. */
export function DateField({ now, value, onChange, ...fieldProps }: DateFieldProps) {
  return (
    <div className="flex flex-col gap-3">
      <div role="group" aria-label="Quick choices" className="flex flex-wrap gap-2">
        {MUTE_PRESETS.map((preset) => {
          const presetDate = dateAfterDays(now, preset.days);
          const selected = value === presetDate;
          return (
            <Button
              key={preset.days}
              size="sm"
              aria-pressed={selected}
              className={cn(selected && "border-accent bg-surface-selected")}
              onClick={() => {
                onChange(presetDate);
              }}
            >
              {preset.label}
            </Button>
          );
        })}
      </div>
      <DateInput
        {...fieldProps}
        type="date"
        name="date"
        value={value}
        min={toDateInput(new Date(now.getFullYear(), now.getMonth(), now.getDate() + 1))}
        max={maxMuteDate(now)}
        onChange={(event) => {
          onChange(event.target.value);
        }}
      />
    </div>
  );
}
