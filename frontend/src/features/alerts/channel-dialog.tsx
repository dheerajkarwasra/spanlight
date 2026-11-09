import { useState, type ReactElement } from "react";

import { Dialog, DialogContent, DialogTrigger } from "@/components/ui/dialog";
import type { AlertChannel, AlertChannelWithSecret } from "@/lib/api";

import { ChannelForm } from "./channel-form";
import { SecretReveal } from "./secret-reveal";

interface ChannelDialogProps {
  /** Null to add a channel; a channel to edit it. */
  channel: AlertChannel | null;
  /** The button that opens the dialog (Add channel). Omitted when the caller controls `open`. */
  trigger?: ReactElement;
  open?: boolean;
  onOpenChange?: (open: boolean) => void;
  /** Called once a new channel is saved (never on an edit), without its secret. */
  onCreated?: (channel: AlertChannel) => void;
}

/**
 * Figma "Alerts — Channel dialog" (email, Slack, webhook, PagerDuty; create and edit). A new
 * webhook channel turns the dialog into the one-time signing-secret reveal. The form is mounted
 * only while open, so a typed secret never outlives the dialog.
 */
export function ChannelDialog({
  channel,
  trigger,
  open,
  onOpenChange,
  onCreated,
}: ChannelDialogProps) {
  const [ownOpen, setOwnOpen] = useState(false);
  const [pending, setPending] = useState(false);
  const [created, setCreated] = useState<AlertChannelWithSecret | null>(null);
  const isOpen = open ?? ownOpen;
  const revealing = created?.secret != null;

  function handleOpenChange(next: boolean) {
    // Escape, Cancel, the close mark and an outside click all end up here: none of them may close
    // the dialog while the channel is being saved.
    if (!next && pending) {
      return;
    }
    setOpen(next);
  }

  function setOpen(next: boolean) {
    setOwnOpen(next);
    onOpenChange?.(next);
    if (!next) {
      setCreated(null);
    }
  }

  return (
    <Dialog open={isOpen} onOpenChange={handleOpenChange}>
      {trigger ? <DialogTrigger asChild>{trigger}</DialogTrigger> : null}
      {isOpen ? (
        <DialogContent
          hideClose
          returnFocus={!trigger}
          className="max-h-[calc(100dvh-2rem)] max-w-[520px] gap-0 overflow-y-auto rounded-card p-6 sm:p-7"
          onInteractOutside={(event) => {
            // An outside click must not throw away a secret that is shown only once.
            if (revealing) {
              event.preventDefault();
            }
          }}
        >
          {created?.secret ? (
            <SecretReveal
              channel={created}
              secret={created.secret}
              rotated={false}
              onDone={() => {
                setOpen(false);
              }}
            />
          ) : (
            <ChannelForm
              channel={channel}
              onPendingChange={setPending}
              onSaved={(saved) => {
                const { secret, ...savedChannel } = saved;
                if (channel === null) {
                  onCreated?.(savedChannel);
                }
                if (secret) {
                  setCreated(saved);
                } else {
                  setOpen(false);
                }
              }}
            />
          )}
        </DialogContent>
      ) : null}
    </Dialog>
  );
}
