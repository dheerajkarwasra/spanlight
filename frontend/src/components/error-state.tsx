import { AlertTriangle, RotateCw } from "lucide-react";

import { Button } from "@/components/ui/button";
import { errorMessage, isApiError } from "@/lib/api";
import { cn } from "@/lib/utils";

interface ErrorStateProps {
  error: unknown;
  onRetry?: () => void;
  title?: string;
  /** Replaces the error's own message, for a design with fixed copy. */
  description?: string;
  /** A retry is in flight: the button shows a spinner and is disabled. */
  retrying?: boolean;
  className?: string;
  /** Tighter spacing for use inside a card. */
  compact?: boolean;
}

/** A failed request: what went wrong, the request ID for support, and a retry. */
export function ErrorState({
  error,
  onRetry,
  title = "Couldn't load this data",
  description,
  retrying = false,
  className,
  compact = false,
}: ErrorStateProps) {
  const requestId = isApiError(error) ? error.requestId : null;

  return (
    <div
      role="alert"
      className={cn(
        "flex flex-col items-center justify-center gap-4 text-center",
        compact ? "px-4 py-6" : "px-6 py-12",
        className,
      )}
    >
      <div
        className={cn(
          "flex items-center justify-center rounded-2xl bg-danger-subtle",
          compact ? "size-12" : "size-16",
        )}
      >
        <AlertTriangle className={cn("text-danger", compact ? "size-5" : "size-7")} aria-hidden />
      </div>
      <div className="flex max-w-sm flex-col gap-1.5">
        <h3
          className={cn(
            "font-bold tracking-[-0.01em] text-foreground",
            compact ? "text-base" : "text-lg",
          )}
        >
          {title}
        </h3>
        <p className="text-sm text-muted-foreground">{description ?? errorMessage(error)}</p>
        {requestId ? (
          <p className="font-mono text-xs text-muted-foreground">Request ID: {requestId}</p>
        ) : null}
      </div>
      {onRetry ? (
        <Button size="sm" onClick={onRetry} loading={retrying}>
          <RotateCw aria-hidden />
          Try again
        </Button>
      ) : null}
    </div>
  );
}
