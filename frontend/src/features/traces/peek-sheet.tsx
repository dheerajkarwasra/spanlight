import { Link, useRouter } from "@tanstack/react-router";
import { ArrowUpRight, X } from "lucide-react";
import { useEffect, useRef, useState, type KeyboardEvent } from "react";

import { Button } from "@/components/ui/button";
import {
  Sheet,
  SheetClose,
  SheetContent,
  SheetDescription,
  SheetTitle,
} from "@/components/ui/sheet";
import { Tooltip } from "@/components/ui/tooltip";
import { useProjectParams } from "@/features/shell/project-context";

import { rememberOpenedTrace } from "./last-traces-search";
import { stepPeek } from "./peek-nav";
import { PeekBody, StepButton } from "./peek-parts";
import { useTraceQuery } from "./trace-queries";
import type { PeekSelection } from "./trace-view";
import { traceRowLink, type Peek } from "./use-peek";

/** j/k are letters: typing them into a field (the waterfall's search) must not change trace. */
const TYPING = "input, textarea, select, [contenteditable='true']";

/**
 * Figma "Traces / Peek" (spec §3.2): the peeked trace in a right sheet over the list, with its
 * place in the loaded rows, previous/next (j/k while focus is in the sheet), "Open full page"
 * (carrying the span and tab) and Close.
 */
export function PeekSheet({ peek }: { peek: Peek }) {
  return (
    <Sheet
      open={peek.peekId !== undefined}
      onOpenChange={(open) => {
        if (!open) peek.close();
      }}
    >
      {peek.peekId === undefined ? null : <PeekContent peek={peek} peekId={peek.peekId} />}
    </Sheet>
  );
}

function PeekContent({ peek, peekId }: { peek: Peek; peekId: string }) {
  const { orgId, projectId } = useProjectParams();
  const router = useRouter();
  const traceQuery = useTraceQuery(peekId);
  // The span and tab belong to the trace they were picked on; stepping starts the next one fresh.
  const [selection, setSelection] = useState<PeekSelection & { traceId?: string }>({});
  const current = selection.traceId === peekId ? selection : {};
  const [box, setBox] = useState<HTMLDivElement | null>(null);
  const titleRef = useRef<HTMLHeadingElement>(null);
  const { ids } = peek;
  const index = ids.indexOf(peekId);
  const fullPage = {
    to: "/$orgId/$projectId/traces/$traceId",
    params: { orgId, projectId, traceId: peekId },
    search: { span: current.span, tab: current.tab },
  } as const;

  useEffect(() => {
    rememberOpenedTrace(peekId);
    traceRowLink(peekId)?.scrollIntoView({ block: "nearest" });
  }, [peekId]);

  // On the sheet only (WCAG 2.1.4): the keys work while focus is inside it, never page-wide.
  function onKeyDown(event: KeyboardEvent<HTMLDivElement>) {
    // React events bubble through portals: a dialog or popover opened from the sheet is not in it.
    if (!(event.target instanceof Node) || !event.currentTarget.contains(event.target)) return;
    const key = event.key;
    if ((key !== "j" && key !== "k") || event.metaKey || event.ctrlKey || event.altKey) return;
    if (event.target instanceof Element && event.target.closest(TYPING)) return;
    event.preventDefault();
    peek.step(key === "j" ? 1 : -1);
  }

  return (
    <SheetContent
      side="right"
      hideClose
      overlayClassName="backdrop-blur-none"
      className="w-[65vw] max-w-[1100px] overflow-hidden"
      onKeyDown={onKeyDown}
      onOpenAutoFocus={(event) => {
        event.preventDefault();
        titleRef.current?.focus();
      }}
      onCloseAutoFocus={(event) => {
        // Back to the row of the trace shown last (j/k may have moved on from the clicked one).
        const link = traceRowLink(peekId);
        if (link) {
          event.preventDefault();
          link.focus();
        }
      }}
    >
      <header className="flex h-14 shrink-0 items-center gap-2 border-b border-border px-5">
        <SheetTitle
          ref={titleRef}
          tabIndex={-1}
          className="min-w-0 flex-1 truncate text-sm font-semibold text-foreground outline-none"
        >
          {traceQuery.data ? (traceQuery.data.name ?? "Unnamed trace") : "Trace"}
        </SheetTitle>
        <SheetDescription className="sr-only">
          Press J or K for the next or previous trace in the list.
        </SheetDescription>
        {index >= 0 ? (
          <span className="shrink-0 text-xs text-muted-foreground tabular">
            {index + 1} of {ids.length}
          </span>
        ) : null}
        <StepButton
          shortcut="k"
          disabled={stepPeek(ids, peekId, -1) === null}
          onClick={() => {
            peek.step(-1);
          }}
        />
        <StepButton
          shortcut="j"
          disabled={stepPeek(ids, peekId, 1) === null}
          onClick={() => {
            peek.step(1);
          }}
        />
        <Button asChild size="sm">
          <Link {...fullPage}>
            <ArrowUpRight aria-hidden />
            Open full page
          </Link>
        </Button>
        <Tooltip content="Close">
          <SheetClose asChild>
            <Button size="icon-sm" aria-label="Close">
              <X aria-hidden />
            </Button>
          </SheetClose>
        </Tooltip>
      </header>
      <div ref={setBox} className="min-h-0 flex-1 overflow-y-auto px-5 pb-5">
        <PeekBody
          traceQuery={traceQuery}
          selection={current}
          onSelectionChange={(next) => {
            setSelection({ ...next, traceId: peekId });
          }}
          scrollElement={box}
          linkHref={router.buildLocation(fullPage).href}
        />
      </div>
    </SheetContent>
  );
}
