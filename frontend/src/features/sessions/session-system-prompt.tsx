import { useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { DiffColumn, stepDiff } from "@/features/traces";

import { promptChange } from "./system-prompt";

/** About three lines of the conversation column's narrowest width. */
const CLAMP_CHARS = 180;

/** Spec §8 S4: turn 1's system prompt, clamped to 3 lines until "Show all". */
export function SystemPromptBlock({ text }: { text: string }) {
  const [expanded, setExpanded] = useState(false);
  return (
    <div className="flex flex-col gap-1 rounded-tile border border-dashed border-border px-4 py-3">
      <p className="text-overline text-subtle-foreground uppercase">System prompt</p>
      <p
        className={
          expanded
            ? "text-xs break-words whitespace-pre-wrap text-muted-foreground"
            : "line-clamp-3 text-xs break-words whitespace-pre-wrap text-muted-foreground"
        }
      >
        {text}
      </p>
      {/* shortcut: guesses the clamp from the text, not the rendered height; fine for prompts. */}
      {text.split("\n").length > 3 || text.length > CLAMP_CHARS ? (
        <Button
          variant="link"
          size="sm"
          aria-expanded={expanded}
          onClick={() => {
            setExpanded((current) => !current);
          }}
          className="self-start"
        >
          {expanded ? "Show less" : "Show all"}
        </Button>
      ) : null}
    </div>
  );
}

/**
 * Spec §8 S4 on later turns: "System prompt unchanged", or "System prompt changed" opening a line
 * diff against turn 1. Nothing when either prompt is unknown.
 */
export function SystemPromptBadge({
  first,
  current,
}: {
  first: string | null;
  current: string | null;
}) {
  const change = promptChange(first, current);
  if (change === null || first === null || current === null) return null;
  if (change === "unchanged") {
    return <Badge className="self-start">System prompt unchanged</Badge>;
  }
  return (
    <Popover>
      <PopoverTrigger asChild>
        <button type="button" className="min-h-6 self-start rounded-full">
          <Badge variant="warning">System prompt changed</Badge>
        </button>
      </PopoverTrigger>
      <PopoverContent className="flex max-h-96 w-[min(32rem,calc(100vw-2rem))] flex-col gap-2 overflow-y-auto">
        <PromptDiff first={first} current={current} />
      </PopoverContent>
    </Popover>
  );
}

/** Rendered only while the popover is open, so the diff is computed only when asked for. */
function PromptDiff({ first, current }: { first: string; current: string }) {
  const { items, cut } = stepDiff(first, current);
  return (
    <>
      <p className="text-sm font-semibold">System prompt since turn 1</p>
      <DiffColumn label="System prompt changes since turn 1" items={items} />
      {cut ? (
        <p className="text-xs text-muted-foreground">Only the first 16 KB of each is compared.</p>
      ) : null}
    </>
  );
}
