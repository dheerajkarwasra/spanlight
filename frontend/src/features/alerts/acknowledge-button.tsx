import { UserCheck } from "lucide-react";
import { useRef } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { errorMessage, isApiError, type AlertEvent } from "@/lib/api";
import { cn } from "@/lib/utils";

import { compactRelative } from "./alert-time";
import { useAcknowledgeEvent } from "./alerts-queries";
import { TimeText } from "./time-text";
import { personName } from "./timeline-markers";

interface AcknowledgeButtonProps {
  /** The rule's open event. */
  event: AlertEvent;
  canWrite: boolean;
  now: Date;
  className?: string;
}

/**
 * "Acknowledge" on the open event; once someone has, the spec's done line in its place:
 * "Acknowledged by {name} {relative time}". Without `alerts:write` the button is hidden; the done
 * line still shows.
 */
export function AcknowledgeButton({ event, canWrite, now, className }: AcknowledgeButtonProps) {
  const acknowledge = useAcknowledgeEvent();
  // Set by a click here: the done line that replaces the button then takes focus (WCAG 2.4.3).
  const focusDoneLine = useRef(false);

  if (event.acknowledged_at !== null) {
    const by = event.acknowledged_by;
    return (
      <p
        role="status"
        tabIndex={-1}
        ref={(node) => {
          if (node && focusDoneLine.current) {
            focusDoneLine.current = false;
            node.focus();
          }
        }}
        className={cn(
          "outline-none",
          "flex items-center gap-2 text-sm font-medium text-muted-foreground",
          className,
        )}
      >
        <UserCheck aria-hidden className="size-4 shrink-0 text-accent" strokeWidth={2} />
        <span>
          Acknowledged by {by ? personName(by) : "a removed account"}{" "}
          <TimeText
            iso={event.acknowledged_at}
            text={compactRelative(event.acknowledged_at, now)}
          />
        </span>
      </p>
    );
  }

  if (!canWrite) {
    return null;
  }

  return (
    <Button
      variant="primary"
      className={className}
      loading={acknowledge.isPending}
      onClick={() => {
        acknowledge.mutate(event.id, {
          onSuccess: () => {
            focusDoneLine.current = true;
            toast.success(`Acknowledged "${event.rule_name}".`);
          },
          onError: (error) => {
            // Someone else got there first: the refetch shows who, so it isn't an error here.
            if (isApiError(error) && error.code === "ALREADY_ACKNOWLEDGED") {
              focusDoneLine.current = true;
            } else {
              toast.error(errorMessage(error));
            }
          },
        });
      }}
    >
      Acknowledge
    </Button>
  );
}
