import { RadioGroup as RadioGroupPrimitive } from "radix-ui";
import { useId, useState } from "react";

import { Callout } from "@/components/callout";
import { DialogHeading } from "@/components/dialog-heading";
import { ErrorState } from "@/components/error-state";
import { Notice } from "@/components/notice";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Dialog, DialogClose, DialogContent, DialogTrigger } from "@/components/ui/dialog";
import { Skeleton } from "@/components/ui/skeleton";
import { UnknownValue } from "@/components/unknown-value";
import { errorMessage, type Sensitivity, type SensitivityLevel } from "@/lib/api";
import { formatInteger } from "@/lib/format";
import { cn } from "@/lib/utils";

import { useSensitivityPreview, useSetSensitivity } from "./doctor-queries";
import { insightKindLabel } from "./insight-format";
import {
  gateText,
  gateValue,
  isStricter,
  levelDescription,
  levelLabel,
  LEVELS,
} from "./sensitivity-format";

const PREVIEW_CAPTION = "Findings it would have opened";
const WINDOWS = [7, 30] as const;

/**
 * Figma "Doctor/Sensitivity dialog" behind a row's "Change" button: the three settings with their
 * gates, what each would have opened over 7 and 30 days, and the resolve note when the choice is
 * stricter (spec §4.4). The button is the dialog's trigger, so closing returns focus to it.
 */
export function SensitivityDialog({ item }: { item: Sensitivity }) {
  const [open, setOpen] = useState(false);
  const name = insightKindLabel(item.detector);
  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button size="sm" aria-label={`Change sensitivity for ${name}`}>
          Change
        </Button>
      </DialogTrigger>
      {/* Mounted only while open, so the choice starts at the check's current setting. */}
      {open ? (
        <SensitivityDialogBody
          item={item}
          onDone={() => {
            setOpen(false);
          }}
        />
      ) : null}
    </Dialog>
  );
}

function SensitivityDialogBody({ item, onDone }: { item: Sensitivity; onDone: () => void }) {
  const [choice, setChoice] = useState<SensitivityLevel>(item.level);
  const save = useSetSensitivity();
  const name = insightKindLabel(item.detector);
  const legendId = useId();
  const keepOpen = (event: Event) => {
    if (save.isPending) {
      event.preventDefault();
    }
  };

  return (
    <DialogContent
      hideClose
      onEscapeKeyDown={keepOpen}
      onInteractOutside={keepOpen}
      className="max-h-[calc(100dvh-2rem)] max-w-[540px] gap-5 overflow-y-auto rounded-card p-6 sm:p-7"
    >
      <DialogHeading
        title={`Sensitivity · ${name}`}
        description="Choose how strong a signal must be before Doctor opens a finding."
        showClose={save.isPending ? "disabled" : true}
      />
      <form
        className="flex flex-col gap-5"
        onSubmit={(event) => {
          event.preventDefault();
          // The same setting again changes nothing: no request, no audit event.
          if (choice === item.level) {
            onDone();
            return;
          }
          save.mutate({ detector: item.detector, level: choice }, { onSuccess: onDone });
        }}
      >
        <fieldset className="flex flex-col gap-2">
          <legend id={legendId} className="sr-only">
            Setting
          </legend>
          <RadioGroupPrimitive.Root
            aria-labelledby={legendId}
            value={choice}
            onValueChange={(value) => {
              setChoice(value as SensitivityLevel);
            }}
            className="flex flex-col gap-2"
          >
            {LEVELS.map((level) => (
              <LevelOption key={level} item={item} level={level} checked={level === choice} />
            ))}
          </RadioGroupPrimitive.Root>
        </fieldset>
        <PreviewTable item={item} choice={choice} />
        {isStricter(item.level, choice) ? (
          <Notice>
            Open {name.toLowerCase()} findings below {gateValue(item, choice)} resolve after 24
            hours without a new detection.
          </Notice>
        ) : null}
        {save.isError ? (
          <Callout tone="danger" role="alert">
            {errorMessage(save.error)}
          </Callout>
        ) : null}
        <div className="flex justify-end gap-2.5">
          <DialogClose asChild>
            <Button disabled={save.isPending}>Cancel</Button>
          </DialogClose>
          <Button type="submit" variant="primary" loading={save.isPending}>
            Save setting
          </Button>
        </div>
      </form>
    </DialogContent>
  );
}

interface LevelOptionProps {
  item: Sensitivity;
  level: SensitivityLevel;
  checked: boolean;
}

/** One setting: the whole tile selects it; arrow keys move between them (Radix RadioGroup). */
function LevelOption({ item, level, checked }: LevelOptionProps) {
  const id = useId();
  const gate = gateText(item, level);
  return (
    <label
      className={cn(
        "flex cursor-pointer items-start gap-3 rounded-tile border bg-surface p-4 transition-colors",
        checked
          ? "border-foreground ring-1 ring-foreground"
          : "border-border hover:bg-surface-hover",
      )}
    >
      <RadioGroupPrimitive.Item
        value={level}
        aria-labelledby={`${id}-name`}
        aria-describedby={`${id}-desc`}
        className="mt-0.5 flex size-5 shrink-0 items-center justify-center rounded-full border border-border-strong bg-surface outline-none focus-visible:outline-[1.5px] focus-visible:outline-offset-2 focus-visible:outline-ring data-[state=checked]:border-foreground"
      >
        <RadioGroupPrimitive.Indicator className="size-2.5 rounded-full bg-foreground" />
      </RadioGroupPrimitive.Item>
      <span className="flex min-w-0 flex-col gap-0.5">
        <span id={`${id}-name`} className="flex items-center gap-2">
          <span className="text-sm font-semibold text-foreground">{levelLabel(level)}</span>
          {level === item.level ? <Badge size="sm">Current</Badge> : null}
        </span>
        <span id={`${id}-desc`} className="flex flex-col gap-0.5">
          <span className="text-sm text-muted-foreground">{levelDescription(level)}</span>
          <span className="text-xs font-medium text-foreground">
            {level === "high_recall" ? gate : `Opens at ${gate}`}
          </span>
        </span>
      </span>
    </label>
  );
}

/** Rows are the settings, columns the windows; the chosen row is tinted, the current one named. */
function PreviewTable({ item, choice }: { item: Sensitivity; choice: SensitivityLevel }) {
  const preview = useSensitivityPreview(item.detector, true);
  if (preview.isError) {
    return (
      <div className="flex flex-col gap-2">
        <h3 className="text-sm font-semibold text-foreground">{PREVIEW_CAPTION}</h3>
        <div className="rounded-tile border border-border">
          <ErrorState
            compact
            error={preview.error}
            title="Preview didn’t load"
            description="You can still save; the counts only inform the choice."
            retrying={preview.isFetching}
            onRetry={() => {
              void preview.refetch();
            }}
          />
        </div>
      </div>
    );
  }
  const windows = preview.data?.windows;
  return (
    <div className="flex flex-col gap-2">
      <table aria-busy={preview.isPending} className="w-full text-sm tabular">
        <caption className="pb-2 text-left text-sm font-semibold text-foreground">
          {PREVIEW_CAPTION}
        </caption>
        <thead className="bg-surface-muted text-xs font-medium text-muted-foreground">
          <tr>
            <th scope="col" className="px-3 py-2 text-left font-medium">
              Setting
            </th>
            {WINDOWS.map((days) => (
              <th key={days} scope="col" className="px-3 py-2 text-right font-medium">
                Last {days} days
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {LEVELS.map((level) => (
            <tr
              key={level}
              className={cn("border-t border-border", level === choice && "bg-surface-muted")}
            >
              <th scope="row" className="px-3 py-2.5 text-left font-medium text-foreground">
                {levelLabel(level)}
                {level === item.level ? " (current)" : null}
              </th>
              {WINDOWS.map((days) => {
                const count = windows?.find((window) => window.days === days)?.[level];
                return (
                  <td key={days} className="px-3 py-2.5 text-right text-foreground">
                    {preview.isPending ? (
                      <Skeleton className="ml-auto h-4 w-8" />
                    ) : (
                      (formatInteger(count) ?? (
                        <UnknownValue reason="Not counted for this window." />
                      ))
                    )}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
      <p className="text-xs text-muted-foreground">
        Based on findings Doctor opened. A stricter setting can only remove findings, so this is
        what it would have shown.
      </p>
    </div>
  );
}
