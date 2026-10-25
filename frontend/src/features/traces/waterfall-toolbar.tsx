import { ChevronsDownUp, ChevronsUpDown, Repeat2, Search } from "lucide-react";
import { useId } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { SegmentedControl } from "@/components/ui/segmented-control";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { Tooltip } from "@/components/ui/tooltip";
import type { SpanKind } from "@/lib/api";
import { formatInteger, pluralize } from "@/lib/format";
import { cn } from "@/lib/utils";

import { kindMeta } from "./span-kind-meta";

const ALL = "all";

interface WaterfallToolbarProps {
  spanCount: number;
  failedCount: number;
  query: string;
  onQueryChange: (query: string) => void;
  /** Enter in the search box. */
  onNextMatch: () => void;
  /** 1-based position of the selected span among the matches; 0 when it isn't one. */
  matchIndex: number;
  matchCount: number;
  kinds: SpanKind[];
  kind: SpanKind | null;
  onKindChange: (kind: SpanKind | null) => void;
  /** Null when the trace has no retry group, which hides the switch. */
  collapseRetries: boolean | null;
  onCollapseRetriesChange: (on: boolean) => void;
  /** Null when no span has children. */
  onExpandAll: (() => void) | null;
  onCollapseAll: () => void;
}

function matchLabel(index: number, count: number): string {
  if (count === 0) {
    return "No match";
  }
  return index > 0
    ? `${formatInteger(index)} of ${formatInteger(count)}`
    : `${formatInteger(count)} found`;
}

/**
 * Figma "Trace detail" tree toolbar (spec §6 D7), one 36 px row: span count, find, kind filter,
 * retries, expand/collapse. The kind filter is pills where the tree pane is wide enough (container
 * query on the pane) and a "Kind: All" select otherwise.
 */
export function WaterfallToolbar(props: WaterfallToolbarProps) {
  const switchId = useId();
  const kindOptions = [
    { value: ALL, label: "All" },
    ...props.kinds.map((kind) => ({ value: kind, label: kindMeta(kind).label })),
  ];
  const kindValue = props.kind ?? ALL;
  const onKind = (value: string) => {
    props.onKindChange(props.kinds.find((kind) => kind === value) ?? null);
  };
  return (
    <div data-slot="tree-toolbar" className="flex flex-wrap items-center gap-1.5">
      <h2 className="text-sm font-semibold whitespace-nowrap text-foreground tabular">
        {pluralize(props.spanCount, "span")}
        {props.failedCount > 0 ? (
          // Visible once the pane has room; until then the failed rows carry their own icon.
          <span className="sr-only text-danger-text @min-[32rem]:not-sr-only">
            {" "}
            · {formatInteger(props.failedCount)} failed
          </span>
        ) : null}
      </h2>
      <div className="relative min-w-16 flex-1">
        <Search
          aria-hidden
          className="pointer-events-none absolute top-1/2 left-2.5 size-4 -translate-y-1/2 text-subtle-foreground"
        />
        <Input
          type="search"
          aria-label="Find span"
          placeholder="Find"
          value={props.query}
          onChange={(event) => {
            props.onQueryChange(event.target.value);
          }}
          onKeyDown={(event) => {
            if (event.key === "Enter") {
              event.preventDefault();
              props.onNextMatch();
            }
          }}
          className={cn("h-9 rounded-full pl-8", props.query.trim() ? "pr-20" : "pr-2")}
        />
        <span
          role="status"
          className="absolute top-1/2 right-3 -translate-y-1/2 text-xs text-muted-foreground tabular"
        >
          {props.query.trim() ? matchLabel(props.matchIndex, props.matchCount) : null}
        </span>
      </div>
      {props.kinds.length > 1 ? (
        <>
          <SegmentedControl
            aria-label="Span kind"
            tone="surface"
            value={kindValue}
            onValueChange={onKind}
            options={kindOptions}
            className="hidden @min-[44rem]:inline-flex"
          />
          <Select value={kindValue} onValueChange={onKind}>
            <SelectTrigger
              aria-label="Kind"
              className="h-9 w-auto shrink-0 gap-1 rounded-full px-2.5 @min-[44rem]:hidden"
            >
              <SelectValue>
                Kind: {kindOptions.find((option) => option.value === kindValue)?.label}
              </SelectValue>
            </SelectTrigger>
            <SelectContent>
              {kindOptions.map((option) => (
                <SelectItem key={option.value} value={option.value}>
                  {option.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </>
      ) : null}
      {props.collapseRetries !== null ? (
        <div className="flex items-center gap-1.5">
          <Switch
            id={switchId}
            aria-label="Collapse retries"
            checked={props.collapseRetries}
            onCheckedChange={props.onCollapseRetriesChange}
          />
          {/* A narrow tree pane shows a retry icon for the label so the toolbar stays one row. */}
          <Tooltip content="Collapse retries">
            <Label htmlFor={switchId} className="font-medium whitespace-nowrap">
              <span className="hidden @min-[36rem]:inline">Collapse retries</span>
              <Repeat2 aria-hidden className="size-4 text-muted-foreground @min-[36rem]:hidden" />
            </Label>
          </Tooltip>
        </div>
      ) : null}
      {props.onExpandAll ? (
        <div className="ml-auto flex shrink-0 items-center">
          <Tooltip content="Expand all">
            <Button
              variant="ghost"
              size="icon-sm"
              aria-label="Expand all"
              onClick={props.onExpandAll}
            >
              <ChevronsUpDown />
            </Button>
          </Tooltip>
          <Tooltip content="Collapse all">
            <Button
              variant="ghost"
              size="icon-sm"
              aria-label="Collapse all"
              onClick={props.onCollapseAll}
            >
              <ChevronsDownUp />
            </Button>
          </Tooltip>
        </div>
      ) : null}
    </div>
  );
}
