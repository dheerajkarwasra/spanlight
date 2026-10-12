import { ListFilter, Plus } from "lucide-react";
import { useState, type SubmitEvent } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { cn } from "@/lib/utils";

import { CHIP_BASE } from "./facet-chips";
import type { TraceFilters } from "./search";
import type { FilterPatch } from "./use-trace-filters";

interface AddFilterPopoverProps {
  search: TraceFilters;
  onApply: (patch: FilterPatch) => void;
}

/** Exact-match filters for identifiers that have too many values for a dropdown. */
export function AddFilterPopover({ search, onApply }: AddFilterPopoverProps) {
  const [open, setOpen] = useState(false);
  const [user, setUser] = useState("");
  const [session, setSession] = useState("");

  function handleSubmit(event: SubmitEvent<HTMLFormElement>) {
    event.preventDefault();
    onApply({ user: user.trim(), session: session.trim() });
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
      <PopoverContent className="w-80">
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
