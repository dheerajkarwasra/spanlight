import { useId } from "react";

import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

import { isMetadataValueTooLong, METADATA_VALUE_MAX } from "./metadata-filter";

/** The popover's unsaved metadata filter; no key means no metadata filter. */
export interface MetadataDraft {
  key: string | undefined;
  value: string;
}

const NO_KEY = "__none__";

interface MetadataFilterFieldProps {
  keys: readonly string[];
  draft: MetadataDraft;
  onChange: (draft: MetadataDraft) => void;
}

/**
 * Figma "Field · Metadata key = value": a key select and a value input. An empty value finds the
 * traces without the key, the attribution page's "none" drill-down.
 */
export function MetadataFilterField({ keys, draft, onChange }: MetadataFilterFieldProps) {
  const id = useId();
  const tooLong = isMetadataValueTooLong(draft.value);

  return (
    <div role="group" aria-labelledby={`${id}-label`} className="flex flex-col gap-1.5">
      <span id={`${id}-label`} className="text-label font-semibold text-foreground">
        Metadata key = value
      </span>
      <div className="flex items-center gap-2">
        <Select
          value={draft.key ?? NO_KEY}
          onValueChange={(next) => {
            onChange({ ...draft, key: next === NO_KEY ? undefined : next });
          }}
          disabled={keys.length === 0}
        >
          <SelectTrigger
            aria-label="Metadata key"
            title={keys.length === 0 ? "No metadata keys in the last 7 days" : undefined}
            className="h-11 w-34 shrink-0 px-3 font-mono text-label"
          >
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value={NO_KEY}>Any</SelectItem>
            {keys.map((key) => (
              <SelectItem key={key} value={key} className="font-mono">
                {key}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <span aria-hidden className="text-sm font-semibold text-muted-foreground">
          =
        </span>
        <Input
          aria-label="Metadata value"
          aria-describedby={`${id}-help`}
          aria-invalid={tooLong || undefined}
          value={draft.value}
          placeholder="e.g. acme"
          autoComplete="off"
          disabled={draft.key === undefined}
          className="h-11 min-w-0 flex-1 px-3 font-mono text-label"
          onChange={(event) => {
            onChange({ ...draft, value: event.target.value });
          }}
        />
      </div>
      <p
        id={`${id}-help`}
        className={
          tooLong
            ? "text-xs font-medium text-danger-text"
            : "text-xs font-medium text-muted-foreground"
        }
      >
        {tooLong
          ? `A value has at most ${METADATA_VALUE_MAX} characters.`
          : "Leave the value empty to find traces without the key."}
      </p>
    </div>
  );
}
