import { Link } from "@tanstack/react-router";
import { ChevronRight, KeyRound, Lock } from "lucide-react";

import { useProjectParams } from "@/features/shell/project-context";

interface NotPromotedNoteProps {
  metadataKey: string;
  /** Admins can promote the key; others are told to ask one. */
  canPromote: boolean;
}

/**
 * Figma "Attribution / Picker — not promoted": a key outside the daily rollup over a window
 * longer than 24 hours. It replaces the table and no request is sent (RULING T20-1).
 */
export function NotPromotedNote({ metadataKey, canPromote }: NotPromotedNoteProps) {
  const { orgId, projectId } = useProjectParams();
  return (
    <div className="flex gap-3.5 rounded-card bg-surface-muted p-5">
      <span
        aria-hidden
        className="flex size-9 shrink-0 items-center justify-center rounded-full bg-surface text-accent"
      >
        <KeyRound className="size-4" />
      </span>
      <div className="flex min-w-0 flex-col gap-1.5 text-sm">
        <p className="font-semibold text-foreground">
          <code className="font-mono font-normal">{metadataKey}</code> isn’t promoted, so this
          window can’t use it
        </p>
        <p className="text-muted-foreground">
          Windows over 24 hours read the daily rollup, which holds promoted keys only. Pick 24 hours
          or less to see any key now.
        </p>
        {canPromote ? (
          <Link
            to="/$orgId/$projectId/settings/project"
            params={{ orgId, projectId }}
            className="inline-flex min-h-6 w-fit items-center gap-1 rounded-sm font-semibold text-accent hover:underline"
          >
            Promote
            <ChevronRight aria-hidden className="size-4" />
          </Link>
        ) : (
          <p className="flex items-center gap-1.5 text-muted-foreground">
            <Lock aria-hidden className="size-3.5" />
            <span>
              Ask an admin to promote <code className="font-mono">{metadataKey}</code>
            </span>
          </p>
        )}
      </div>
    </div>
  );
}
