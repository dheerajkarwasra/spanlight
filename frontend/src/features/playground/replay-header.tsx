import { Link } from "@tanstack/react-router";
import { RotateCcw, X } from "lucide-react";

import { Button } from "@/components/ui/button";
import { useProjectParams } from "@/features/shell";
import type { Span } from "@/lib/api";
import { shortId } from "@/lib/format";

/** "Replay from `chat gpt-5.2` · trace 4b0e9c1f" with a link to the source and a close button. */
export function ReplayHeader({
  traceId,
  spanId,
  span,
}: {
  traceId: string;
  spanId: string;
  span: Span | undefined;
}) {
  const { orgId, projectId } = useProjectParams();
  return (
    <div className="flex items-start gap-3">
      <span
        aria-hidden
        className="flex size-7 shrink-0 items-center justify-center rounded-full bg-ink text-ink-foreground"
      >
        <RotateCcw className="size-3.5" />
      </span>
      <div className="flex min-w-0 flex-1 flex-col gap-0.5">
        <h2 className="text-[15px] font-bold text-foreground">Replay</h2>
        <p className="text-xs [overflow-wrap:anywhere] text-muted-foreground">
          from{" "}
          <Link
            to="/$orgId/$projectId/traces/$traceId"
            params={{ orgId, projectId, traceId }}
            search={{ span: spanId }}
            className="rounded-sm font-medium text-accent hover:underline"
          >
            <code className="font-mono">{span?.name ?? shortId(spanId)}</code> · trace{" "}
            {shortId(traceId)}
          </Link>
        </p>
      </div>
      <Button asChild variant="ghost" size="icon-sm">
        <Link
          to="/$orgId/$projectId/playground"
          params={{ orgId, projectId }}
          search={(prev) => ({ ...prev, replay: undefined, edit: undefined })}
          aria-label="Close replay"
        >
          <X aria-hidden />
        </Link>
      </Button>
    </div>
  );
}
