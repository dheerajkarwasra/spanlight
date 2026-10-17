import { Calendar, ChevronDown } from "lucide-react";
import { useRef, useState, type SubmitEvent } from "react";

import { FormField } from "@/components/form-field";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";

import {
  MAX_WINDOW_DAYS,
  todayUtc,
  windowAccessibleName,
  windowDays,
  windowFromDays,
  windowLabel,
  type ReportWindow,
} from "./window-picker";

const PRESET_DAYS = [7, 14, MAX_WINDOW_DAYS] as const;
const DAY_MS = 86_400_000;

interface WindowPickerProps {
  value: ReportWindow;
  /** The new first and last day (inclusive), already checked against the server's rules. */
  onChange: (days: { first: string; last: string }) => void;
}

/** The last `count` whole days before today, as the presets offer them. */
function presetDays(count: number): { first: string; last: string } {
  const today = Date.parse(`${todayUtc()}T00:00:00Z`);
  const day = (ms: number) => new Date(ms).toISOString().slice(0, 10);
  return { first: day(today - count * DAY_MS), last: day(today - DAY_MS) };
}

/**
 * Figma "Bet1/Window picker": a pill naming the window in whole UTC days that opens a popover with
 * presets and a first and last day. A window the server would refuse (over 31 days, or ending
 * after today) is caught here with the server's own wording.
 */
export function WindowPicker({ value, onChange }: WindowPickerProps) {
  const [open, setOpen] = useState(false);
  const [first, setFirst] = useState("");
  const [last, setLast] = useState("");
  const [error, setError] = useState<string | null>(null);
  const lastDay = useRef<HTMLInputElement>(null);
  const today = todayUtc();

  function apply(event: SubmitEvent<HTMLFormElement>) {
    event.preventDefault();
    const next = windowFromDays(first, last);
    if (next.error !== null) {
      // Announced by the alert below, and the field it concerns takes focus (its error is in its
      // description), so a keyboard user lands where the fix goes.
      setError(next.error);
      lastDay.current?.focus();
      return;
    }
    onChange({ first, last });
    setOpen(false);
  }

  function choose(days: { first: string; last: string }) {
    onChange(days);
    setOpen(false);
  }

  return (
    <Popover
      open={open}
      onOpenChange={(next) => {
        setOpen(next);
        if (next) {
          const current = windowDays(value);
          setFirst(current.first);
          setLast(current.last);
          setError(null);
        }
      }}
    >
      <PopoverTrigger asChild>
        <Button
          variant="secondary"
          aria-label={windowAccessibleName(value)}
          className="h-11 w-full justify-start gap-2 pr-3 pl-3.5 shadow-none sm:h-10 sm:w-auto"
        >
          <Calendar aria-hidden className="size-4!" />
          <span className="truncate text-label font-semibold">{windowLabel(value)}</span>
          <span className="ml-auto text-xs font-medium text-subtle-foreground sm:ml-0">UTC</span>
          <ChevronDown aria-hidden className="size-4!" />
        </Button>
      </PopoverTrigger>
      <PopoverContent align="end" className="w-80">
        <div className="flex flex-col gap-4">
          <div className="flex flex-col gap-1">
            <p className="text-card">Window</p>
            <p className="text-xs text-muted-foreground">
              Whole UTC days, up to {MAX_WINDOW_DAYS}, ending today at the latest.
            </p>
          </div>
          <div className="flex flex-wrap gap-2">
            {PRESET_DAYS.map((count) => (
              <Button
                key={count}
                size="sm"
                variant="secondary"
                className="shadow-none"
                onClick={() => {
                  choose(presetDays(count));
                }}
              >
                Last {count} days
              </Button>
            ))}
          </div>
          <form onSubmit={apply} className="flex flex-col gap-4" noValidate>
            <FormField label="First day">
              <Input
                type="date"
                value={first}
                max={today}
                onChange={(event) => {
                  setFirst(event.target.value);
                }}
              />
            </FormField>
            <FormField label="Last day" error={error ?? undefined}>
              <Input
                ref={lastDay}
                type="date"
                value={last}
                max={today}
                onChange={(event) => {
                  setLast(event.target.value);
                }}
              />
            </FormField>
            <p role="alert" className="sr-only">
              {error}
            </p>
            <Button type="submit" variant="primary" className="self-end">
              Apply window
            </Button>
          </form>
        </div>
      </PopoverContent>
    </Popover>
  );
}
