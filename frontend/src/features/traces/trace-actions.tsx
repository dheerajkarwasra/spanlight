import { ClipboardCheck, Database, type LucideIcon } from "lucide-react";

import { Button } from "@/components/ui/button";
import { TooltipContent, TooltipRoot, TooltipTrigger } from "@/components/ui/tooltip";
import { AddToDatasetDialog, AddToQueueDialog } from "@/features/datasets";
import { usePermission } from "@/features/shell";
import type { Span, TraceDetail } from "@/lib/api";

interface TraceActionsProps {
  trace: TraceDetail;
  /** The span selected in the tree; both dialogs offer it. */
  selectedSpan: Span | null;
}

/**
 * The summary bar's "Add to dataset" and "Add to queue": icon-only buttons with a tooltip and an
 * `aria-label`. Neither shows without its permission.
 */
export function TraceActions({ trace, selectedSpan }: TraceActionsProps) {
  const canAddToDataset = usePermission("datasets:write");
  const canAddToQueue = usePermission("annotations:write");
  const ref = { trace_id: trace.trace_id, name: trace.name };

  return (
    <>
      {canAddToDataset ? (
        <TooltipRoot>
          <AddToDatasetDialog
            target={{ kind: "trace", trace, selectedSpan }}
            trigger={<IconTrigger icon={Database} label="Add to dataset" />}
          />
          <TooltipContent>Add to dataset</TooltipContent>
        </TooltipRoot>
      ) : null}
      {canAddToQueue ? (
        <TooltipRoot>
          <AddToQueueDialog
            traces={[ref]}
            selectedSpan={selectedSpan}
            trigger={<IconTrigger icon={ClipboardCheck} label="Add to queue" />}
          />
          <TooltipContent>Add to queue</TooltipContent>
        </TooltipRoot>
      ) : null}
    </>
  );
}

/** A dialog trigger that is also its tooltip's trigger (the dialog's `asChild` passes through). */
function IconTrigger({ icon: Icon, label, ...props }: { icon: LucideIcon; label: string }) {
  return (
    <TooltipTrigger asChild>
      <Button size="icon-sm" aria-label={label} {...props}>
        <Icon aria-hidden />
      </Button>
    </TooltipTrigger>
  );
}
