import { CalendarDays, ChevronDown } from "lucide-react";
import { useId, useState, type SubmitEvent } from "react";

import { Button } from "@/components/ui/button";
import { DateInput } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { cn } from "@/lib/utils";

import { auditDateIssue, describeAuditDates } from "./audit-filters";
import { PILL_ACTIVE, PILL_BASE } from "./audit-filter-pill";

interface AuditDateRange {
  since: string | undefined;
  until: string | undefined;
}

interface AuditDateFilterProps extends AuditDateRange {
  onApply: (range: AuditDateRange) => void;
}

/**
 * The audit log's date pill (Figma "Settings/Filter pill", `kind=date`): "Any time", or the days
 * chosen, violet once set. It opens a small form with a first and a last day; both are inclusive
 * and in the viewer's local time, and either can be left empty for an open end.
 */
export function AuditDateFilter({ since, until, onApply }: AuditDateFilterProps) {
  const [open, setOpen] = useState(false);
  const active = since !== undefined || until !== undefined;
  const summary = describeAuditDates(since, until);

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <Button
          variant="secondary"
          aria-label={`Date range: ${summary}`}
          className={cn(
            PILL_BASE,
            "gap-1.5 px-3 font-semibold [&_svg]:size-4",
            active && PILL_ACTIVE,
          )}
        >
          <CalendarDays aria-hidden />
          <span>{summary}</span>
          <ChevronDown aria-hidden />
        </Button>
      </PopoverTrigger>
      <PopoverContent className="w-72">
        {/* The popover unmounts its content on close, so each opening starts from what is applied. */}
        <DateRangeForm
          initial={{ since, until }}
          onApply={(range) => {
            onApply(range);
            setOpen(false);
          }}
        />
      </PopoverContent>
    </Popover>
  );
}

interface DateRangeFormProps {
  initial: AuditDateRange;
  onApply: (range: AuditDateRange) => void;
}

function DateRangeForm({ initial, onApply }: DateRangeFormProps) {
  const [since, setSince] = useState(initial.since ?? "");
  const [until, setUntil] = useState(initial.until ?? "");
  const [issue, setIssue] = useState<string | null>(null);
  const issueId = useId();

  function handleSubmit(event: SubmitEvent<HTMLFormElement>) {
    event.preventDefault();
    const problem = auditDateIssue(since || undefined, until || undefined);
    if (problem) {
      setIssue(problem);
      return;
    }
    onApply({ since: since || undefined, until: until || undefined });
  }

  return (
    <form onSubmit={handleSubmit} className="flex flex-col gap-4" noValidate>
      <div className="flex flex-col gap-1">
        <p className="text-card">Date range</p>
        <p className="text-xs text-muted-foreground">
          First and last day, in your local time. Leave one empty for no limit.
        </p>
      </div>
      <div className="flex flex-col gap-2">
        <Label htmlFor="audit-since">From</Label>
        <DateInput
          id="audit-since"
          type="date"
          aria-invalid={issue !== null}
          aria-describedby={issue ? issueId : undefined}
          value={since}
          onChange={(event) => {
            setSince(event.target.value);
            setIssue(null);
          }}
        />
      </div>
      <div className="flex flex-col gap-2">
        <Label htmlFor="audit-until">To</Label>
        <DateInput
          id="audit-until"
          type="date"
          aria-invalid={issue !== null}
          aria-describedby={issue ? issueId : undefined}
          value={until}
          onChange={(event) => {
            setUntil(event.target.value);
            setIssue(null);
          }}
        />
      </div>
      {issue ? (
        <p id={issueId} role="alert" className="text-xs font-medium text-danger-text">
          {issue}
        </p>
      ) : null}
      <div className="flex items-center justify-between gap-2">
        <Button
          variant="ghost"
          size="sm"
          onClick={() => {
            onApply({ since: undefined, until: undefined });
          }}
        >
          Any time
        </Button>
        <Button type="submit" variant="primary" size="sm">
          Apply
        </Button>
      </div>
    </form>
  );
}
