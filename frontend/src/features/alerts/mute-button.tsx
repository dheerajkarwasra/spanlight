import { useState } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { errorMessage, type AlertRule } from "@/lib/api";

import { useMuteRule } from "./alerts-queries";
import { MuteDialog } from "./mute-dialog";
import { mutedUntil } from "./rule-state";

interface MuteButtonProps {
  rule: AlertRule;
  now: Date;
  className?: string;
}

/** "Mute" opens the mute dialog; on a muted rule, "Unmute" ends the mute right away. */
export function MuteButton({ rule, now, className }: MuteButtonProps) {
  const [open, setOpen] = useState(false);
  const mute = useMuteRule();

  const muted = mutedUntil(rule, now) !== null;

  return (
    <>
      {muted ? (
        <Button
          className={className}
          loading={mute.isPending}
          onClick={() => {
            mute.mutate(
              { ruleId: rule.id, until: null },
              {
                onSuccess: () => toast.success(`Unmuted "${rule.name}".`),
                onError: (error) => toast.error(errorMessage(error)),
              },
            );
          }}
        >
          Unmute
        </Button>
      ) : (
        <Button
          className={className}
          onClick={() => {
            setOpen(true);
          }}
        >
          Mute
        </Button>
      )}
      {/* Kept mounted across the switch to "Unmute", so the dialog can close normally. */}
      <MuteDialog rule={rule} open={open} onOpenChange={setOpen} />
    </>
  );
}
