import { ArrowRight, Tag } from "lucide-react";
import { useId, useState } from "react";

import { DisabledReason } from "@/components/disabled-reason";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { formatInteger, formatRelativeTime } from "@/lib/format";
import type { ReleaseStats } from "@/lib/api";

export const SAME_RELEASE_NOTICE =
  "Baseline and candidate are the same release. Choose two different releases to compare.";
const PICK_REASON = "Choose a baseline and a candidate to compare.";
const UNAVAILABLE = "Unavailable for this range";
const FAILED = "Couldn't load releases";

interface ReleasePickerProps {
  /** `undefined` while the list loads, or when it failed to (`failed`). */
  releases: readonly ReleaseStats[] | undefined;
  /** The list failed to load: the selects are disabled instead of loading forever. */
  failed: boolean;
  /** The releases in the URL. The parent re-keys the picker when they change. */
  a: string | undefined;
  b: string | undefined;
  /** The window is unusable (over 30 days): the selects are disabled. */
  unavailable: boolean;
  onCompare: (a: string, b: string) => void;
}

/** Figma "Compare releases" card: baseline and candidate selects, then the Compare button. */
export function ReleasePicker({
  releases,
  failed,
  a,
  b,
  unavailable,
  onCompare,
}: ReleasePickerProps) {
  const [draftA, setDraftA] = useState(a);
  const [draftB, setDraftB] = useState(b);
  const same = draftA !== undefined && draftA === draftB;
  const ready = draftA !== undefined && draftB !== undefined && !same && !unavailable;

  return (
    <Card className="flex flex-col gap-3 p-5 sm:p-6">
      <div className="flex flex-col gap-4 sm:flex-row sm:items-end">
        <ReleaseSelect
          label="Baseline (A)"
          value={draftA}
          onChange={setDraftA}
          releases={releases}
          failed={failed}
          unavailable={unavailable}
        />
        <ArrowRight
          aria-hidden
          className="hidden size-4 shrink-0 self-center text-muted-foreground sm:mt-6 sm:block"
        />
        <ReleaseSelect
          label="Candidate (B)"
          value={draftB}
          onChange={setDraftB}
          releases={releases}
          failed={failed}
          unavailable={unavailable}
        />
        {ready ? (
          <Button size="lg" className="max-sm:w-full" onClick={() => onCompare(draftA, draftB)}>
            Compare
          </Button>
        ) : same ? (
          // The line under the row already says why, so no second tooltip or screen-reader text.
          <Button size="lg" disabled className="max-sm:w-full">
            Compare
          </Button>
        ) : (
          <DisabledReason reason={PICK_REASON}>
            <Button size="lg" disabled className="max-sm:w-full">
              Compare
            </Button>
          </DisabledReason>
        )}
      </div>
      {same ? (
        <p role="status" className="text-xs font-medium text-muted-foreground">
          {SAME_RELEASE_NOTICE}
        </p>
      ) : null}
    </Card>
  );
}

interface ReleaseSelectProps {
  label: string;
  value: string | undefined;
  onChange: (release: string) => void;
  releases: readonly ReleaseStats[] | undefined;
  failed: boolean;
  unavailable: boolean;
}

function ReleaseSelect({
  label,
  value,
  onChange,
  releases,
  failed,
  unavailable,
}: ReleaseSelectProps) {
  const id = useId();
  const empty = releases !== undefined && releases.length === 0;
  const missing = releases === undefined && (failed || unavailable);
  const known = releases?.some((release) => release.release === value) ?? false;

  return (
    <div className="flex min-w-0 flex-1 flex-col gap-2">
      <Label htmlFor={id}>{label}</Label>
      {releases === undefined && !missing ? (
        <Skeleton className="h-[46px] w-full rounded-input" />
      ) : (
        <Select
          value={value ?? ""}
          onValueChange={onChange}
          disabled={empty || unavailable || missing}
        >
          <SelectTrigger id={id}>
            <SelectValue
              placeholder={
                unavailable
                  ? UNAVAILABLE
                  : missing
                    ? FAILED
                    : empty
                      ? "No releases"
                      : "Select a release"
              }
            />
          </SelectTrigger>
          <SelectContent>
            {value !== undefined && !known ? (
              <SelectItem value={value}>
                <ReleaseOption name={value} meta={null} />
              </SelectItem>
            ) : null}
            {(releases ?? []).map((release) => (
              <SelectItem key={release.release} value={release.release}>
                <ReleaseOption name={release.release} meta={metaLine(release)} />
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      )}
    </div>
  );
}

function metaLine(release: ReleaseStats): string {
  const seen = formatRelativeTime(release.last_seen_at);
  const traces = `${formatInteger(release.traces) ?? "0"} traces`;
  return seen === null ? traces : `last seen ${seen} · ${traces}`;
}

function ReleaseOption({ name, meta }: { name: string; meta: string | null }) {
  return (
    <span className="flex min-w-0 items-center gap-2">
      <Tag aria-hidden className="size-3.5 shrink-0 text-muted-foreground" />
      <span title={name} className="truncate font-mono text-label">
        {name}
      </span>
      {meta ? (
        <span className="truncate text-xs text-muted-foreground max-sm:hidden">{meta}</span>
      ) : null}
    </span>
  );
}
