import { SegmentedControl } from "@/components/ui/segmented-control";
import type { AttributionGroupBy } from "@/lib/api";

import { GROUP_BY_KINDS } from "./attribution-grid";
import { MetadataKeySelect, type KeyOptions } from "./metadata-key-select";

interface GroupByPickerProps {
  groupBy: AttributionGroupBy;
  metadataKey: string | undefined;
  keys: KeyOptions;
  onChange: (groupBy: AttributionGroupBy, key: string | undefined) => void;
}

/** Figma "Attribution / Picker": the four kinds, and the key menu for Metadata. */
export function GroupByPicker({ groupBy, metadataKey, keys, onChange }: GroupByPickerProps) {
  return (
    <div className="flex flex-col gap-3 md:flex-row md:items-center">
      <SegmentedControl
        aria-label="Group by"
        tone="surface"
        value={groupBy}
        options={GROUP_BY_KINDS}
        onValueChange={(next) => {
          // Metadata opens on the first promoted key, so the page has something to show.
          onChange(next, next === "metadata" ? (metadataKey ?? keys.promoted[0]) : undefined);
        }}
        className="grid grid-cols-2 rounded-tile md:inline-flex md:rounded-full [&>button]:py-2.5 md:[&>button]:py-1.5"
      />
      {groupBy === "metadata" ? (
        <MetadataKeySelect
          value={metadataKey}
          options={keys}
          onChange={(key) => {
            onChange("metadata", key);
          }}
        />
      ) : null}
    </div>
  );
}
