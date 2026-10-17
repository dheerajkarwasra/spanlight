import { Loader2 } from "lucide-react";
import { useEffect, useId, useState } from "react";
import { toast } from "sonner";

import { Tooltip } from "@/components/ui/tooltip";
import { Button } from "@/components/ui/button";
import { errorMessage, isApiError, type BillingSource } from "@/lib/api";
import { cn } from "@/lib/utils";

import { useRefreshSource } from "./billing-sources-queries";
import { retryAfterLabel, retryDeadline } from "./refresh-countdown";
import { TEXT_ACTION_CLASS } from "./source-columns";

interface Wait {
  /** When a refresh may be tried again (epoch ms). */
  deadline: number;
  /** The clock as last read; it ticks every second while waiting. */
  now: number;
}

/** The time left of a `429`'s wait, re-read every second until it has passed. */
function useCountdown(): [string | null, (deadline: number) => void] {
  const [wait, setWait] = useState<Wait | null>(null);
  const waiting = wait !== null;
  useEffect(() => {
    if (!waiting) {
      return;
    }
    const timer = window.setInterval(() => {
      // Once the wait is over the state goes, and with it this timer.
      setWait((current) => {
        const now = Date.now();
        return current && now < current.deadline ? { ...current, now } : null;
      });
    }, 1000);
    return () => {
      window.clearInterval(timer);
    };
  }, [waiting]);
  const label = wait ? retryAfterLabel(wait.deadline, wait.now) : null;
  const start = (deadline: number) => {
    setWait({ deadline, now: Date.now() });
  };
  return [label, start];
}

/**
 * Figma "Refresh" and "Refresh (disabled · rate limited)": queues a fetch of the source now. After
 * a `429` the button counts down to the server's `Retry-After` ("Refresh · 4:12") and refuses
 * clicks until then. It is `aria-disabled`, not `disabled`, so it keeps focus and its reason
 * while a request runs and while it waits.
 */
export function RefreshButton({ source }: { source: BillingSource }) {
  const refresh = useRefreshSource();
  const [countdown, waitUntil] = useCountdown();
  const reasonId = useId();

  const reason = !source.enabled
    ? "This source is turned off. Edit it to turn it on."
    : countdown
      ? `Refresh runs at most once every 5 minutes per source. Try again in ${countdown}.`
      : null;
  const blocked = reason !== null || refresh.isPending;

  function start() {
    if (blocked) {
      return;
    }
    refresh.mutate(source.id, {
      onSuccess: () => {
        // Nothing polls the run, so the toast promises no live update.
        toast.success("Refresh queued. Reload in a minute to see the new figures.");
      },
      onError: (error) => {
        if (isApiError(error) && error.status === 429) {
          waitUntil(retryDeadline(error.retryAfterSeconds, Date.now()));
        } else {
          toast.error(errorMessage(error));
        }
      },
    });
  }

  return (
    <>
      <Tooltip content={reason ?? "Fetch the provider's latest cost report now."}>
        <Button
          variant="secondary"
          aria-label={`Refresh ${source.name}`}
          aria-disabled={blocked || undefined}
          aria-busy={refresh.isPending || undefined}
          aria-describedby={reason ? reasonId : undefined}
          onClick={start}
          className={cn(
            TEXT_ACTION_CLASS,
            "aria-disabled:cursor-not-allowed aria-disabled:opacity-55",
          )}
        >
          {refresh.isPending ? <Loader2 aria-hidden className="animate-spin" /> : null}
          {countdown ? `Refresh · ${countdown}` : "Refresh"}
        </Button>
      </Tooltip>
      <span id={reasonId} className="sr-only">
        {reason}
      </span>
    </>
  );
}
