import { ListFilter, Plus } from "lucide-react";
import { useState, type SubmitEvent } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { cn } from "@/lib/utils";

import { CHIP_BASE } from "./facet-chips";
import { isMetadataValueTooLong } from "./metadata-filter";
import { MetadataFilterField, type MetadataDraft } from "./metadata-filter-field";
import type { TraceFilters } from "./search";
import type { FilterPatch } from "./use-trace-filters";

interface AddFilterPopoverProps {
  search: TraceFilters;
  /** The metadata keys to pick from: promoted first, then the ones seen in the last 7 days. */
  metadataKeys: readonly string[];
  onApply: (patch: FilterPatch) => void;
}

/** Exact-match filters for identifiers that have too many values for a dropdown. */
export function AddFilterPopover({ search, metadataKeys, onApply }: AddFilterPopoverProps) {
  const [open, setOpen] = useState(false);
  const [user, setUser] = useState("");
  const [session, setSession] = useState("");
  const [metadata, setMetadata] = useState<MetadataDraft>({ key: undefined, value: "" });

  function handleSubmit(event: SubmitEvent<HTMLFormElement>) {
    event.preventDefault();
    if (isMetadataValueTooLong(metadata.value)) {
      return;
    }
    onApply({
      user: user.trim(),
      session: session.trim(),
      metadata_key: metadata.key,
      metadata_value: metadata.key !== undefined ? metadata.value : undefined,
    });
    setOpen(false);
  }

  return (
    <Popover
      open={open}
      onOpenChange={(next) => {
        setOpen(next);
        if (next) {
          setUser(search.user ?? "");
          setSession(search.session ?? "");
          setMetadata({ key: search.metadata_key, value: search.metadata_value ?? "" });
        }
      }}
    >
      <PopoverTrigger asChild>
        <Button
          variant="ghost"
          className={cn(CHIP_BASE, "text-muted-foreground hover:text-foreground [&_svg]:size-3.5")}
        >
          <Plus aria-hidden />
          Add filter
        </Button>
      </PopoverTrigger>
      <PopoverContent className="w-90 max-w-[calc(100vw-2rem)]">
        <form onSubmit={handleSubmit} className="flex flex-col gap-3">
          <div className="flex items-center gap-2 text-xs font-medium text-muted-foreground">
            <ListFilter aria-hidden className="size-3.5" />
            Filter by identifier (exact match)
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="trace-filter-user">User ID</Label>
            <Input
              id="trace-filter-user"
              value={user}
              placeholder="e.g. user_123"
              autoComplete="off"
              className="font-mono text-label"
              onChange={(event) => {
                setUser(event.target.value);
              }}
            />
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="trace-filter-session">Session ID</Label>
            <Input
              id="trace-filter-session"
              value={session}
              placeholder="e.g. chat_8f2c"
              autoComplete="off"
              className="font-mono text-label"
              onChange={(event) => {
                setSession(event.target.value);
              }}
            />
          </div>
          <div aria-hidden className="h-px bg-border" />
          <MetadataFilterField keys={metadataKeys} draft={metadata} onChange={setMetadata} />
          <div className="flex justify-end gap-2">
            <Button
              variant="ghost"
              size="sm"
              onClick={() => {
                setOpen(false);
              }}
            >
              Cancel
            </Button>
            <Button type="submit" variant="primary" size="sm">
              Apply filters
            </Button>
          </div>
        </form>
      </PopoverContent>
    </Popover>
  );
}
