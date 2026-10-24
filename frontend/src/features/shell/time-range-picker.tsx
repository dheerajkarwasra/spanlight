import { useNavigate } from "@tanstack/react-router";
import { CalendarClock } from "lucide-react";
import { useId, useState, type SubmitEvent } from "react";

import { Button } from "@/components/ui/button";
import { DateInput } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { SegmentedControl } from "@/components/ui/segmented-control";
import { Tooltip } from "@/components/ui/tooltip";
import {
  describeRange,
  MAX_WINDOW_MS,
  RANGE_LABELS,
  RANGE_PRESETS,
  toLocalInputValue,
  type RangePreset,
} from "@/lib/time-range";
import { cn } from "@/lib/utils";

import { DisabledFilter } from "./disabled-filter";
import { useProjectFilters } from "./project-context";
import { DEFAULT_TIME_RANGE_OPTIONS, type TimeRangeOptions } from "./time-range-options";

interface TimeRangePickerProps {
  /** Defaults to every preset plus the custom range. */
  options?: TimeRangeOptions;
}

/**
 * The time window for project data (Figma "Control/Time range"): a segmented pill for the
 * presets, plus a calendar pill that opens a custom from/to range. Presets the page doesn't offer
 * are drawn disabled with the page's note; a page that can't honour a range at all gets the whole
 * control disabled with the reason. It never rewrites the URL's range.
 */
export function TimeRangePicker({ options = DEFAULT_TIME_RANGE_OPTIONS }: TimeRangePickerProps) {
  const navigate = useNavigate();
  const { range } = useProjectFilters();
  const noteId = useId();
  const { rangeDisabled, presetNote } = options;
  const preset =
    options.presets.find((candidate) => candidate === range.value) ?? options.fallback ?? null;
  const showNote = presetNote !== undefined && rangeDisabled === undefined;

  function selectPreset(next: RangePreset) {
    void navigate({
      to: ".",
      search: (prev) => ({ ...prev, range: next, from: undefined, to: undefined }),
    });
  }

  const control = (
    <div className="flex items-center gap-2">
      <SegmentedControl
        aria-label="Time range"
        aria-describedby={showNote ? noteId : undefined}
        value={preset}
        onValueChange={selectPreset}
        options={RANGE_PRESETS.map((value) => ({
          value,
          label: value,
          srLabel: RANGE_LABELS[value].toLowerCase(),
          disabled: rangeDisabled !== undefined || !options.presets.includes(value),
        }))}
      />
      {options.allowCustom && rangeDisabled === undefined ? (
        <CustomRangePopover />
      ) : (
        <Button variant="secondary" size="icon" aria-label="Custom range" disabled>
          <CalendarClock aria-hidden />
        </Button>
      )}
    </div>
  );

  if (rangeDisabled !== undefined) {
    return (
      <DisabledFilter label="Time range" reason={rangeDisabled}>
        {control}
      </DisabledFilter>
    );
  }
  if (!showNote) {
    return control;
  }
  // Disabled pills take no pointer events, so the note's tooltip hangs on the whole control.
  return (
    <Tooltip content={presetNote}>
      <div>
        {control}
        <span id={noteId} className="sr-only">
          {presetNote}
        </span>
      </div>
    </Tooltip>
  );
}

function CustomRangePopover() {
  const navigate = useNavigate();
  const { range } = useProjectFilters();
  const isCustom = range.value === "custom";
  const [open, setOpen] = useState(false);
  const [customFrom, setCustomFrom] = useState(() => toLocalInputValue(range.from));
  const [customTo, setCustomTo] = useState(() => toLocalInputValue(range.to));
  const [customError, setCustomError] = useState<string | null>(null);

  function applyCustom(event: SubmitEvent<HTMLFormElement>) {
    event.preventDefault();
    const from = new Date(customFrom);
    const to = new Date(customTo);
    if (Number.isNaN(from.getTime()) || Number.isNaN(to.getTime())) {
      setCustomError("Enter both a start and an end time.");
      return;
    }
    if (from >= to) {
      setCustomError("The start must be before the end.");
      return;
    }
    if (to.getTime() - from.getTime() > MAX_WINDOW_MS) {
      setCustomError("Ranges can be at most 90 days long.");
      return;
    }
    setCustomError(null);
    void navigate({
      to: ".",
      search: (prev) => ({
        ...prev,
        range: "custom" as const,
        from: from.toISOString(),
        to: to.toISOString(),
      }),
    });
    setOpen(false);
  }

  return (
    <Popover
      open={open}
      onOpenChange={(next) => {
        setOpen(next);
        if (next) {
          setCustomFrom(toLocalInputValue(range.from));
          setCustomTo(toLocalInputValue(range.to));
          setCustomError(null);
        }
      }}
    >
      <PopoverTrigger asChild>
        <Button
          variant={isCustom ? "primary" : "secondary"}
          size={isCustom ? "md" : "icon"}
          aria-label={isCustom ? `Custom range: ${describeRange(range)}` : "Custom range"}
          className={cn(isCustom && "max-w-64")}
        >
          <CalendarClock aria-hidden />
          {isCustom ? <span className="truncate">{describeRange(range)}</span> : null}
        </Button>
      </PopoverTrigger>
      <PopoverContent align="end" className="w-80">
        <form onSubmit={applyCustom} className="flex flex-col gap-4" noValidate>
          <div className="flex flex-col gap-1">
            <p className="text-card">Custom range</p>
            <p className="text-xs text-muted-foreground">Up to 90 days, in your local time.</p>
          </div>
          <div className="flex flex-col gap-2">
            <Label htmlFor="range-from">From</Label>
            <DateInput
              id="range-from"
              type="datetime-local"
              value={customFrom}
              onChange={(event) => {
                setCustomFrom(event.target.value);
              }}
            />
          </div>
          <div className="flex flex-col gap-2">
            <Label htmlFor="range-to">To</Label>
            <DateInput
              id="range-to"
              type="datetime-local"
              value={customTo}
              onChange={(event) => {
                setCustomTo(event.target.value);
              }}
            />
          </div>
          {customError ? (
            <p role="alert" className="text-xs font-medium text-danger-text">
              {customError}
            </p>
          ) : null}
          <Button type="submit" variant="primary" className="self-end">
            Apply range
          </Button>
        </form>
      </PopoverContent>
    </Popover>
  );
}
