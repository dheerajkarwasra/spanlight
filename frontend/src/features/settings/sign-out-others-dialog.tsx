import { Monitor, Smartphone } from "lucide-react";
import { useState, type ReactElement } from "react";
import { toast } from "sonner";

import { DialogHeading } from "@/components/dialog-heading";
import { RelativeTime } from "@/components/relative-time";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogFooter, DialogTrigger } from "@/components/ui/dialog";
import type { AuthSession } from "@/lib/api";

import { useRevokeOtherAuthSessions } from "./account-queries";
import { describeUserAgent } from "./user-agent";

interface SignOutOthersDialogProps {
  /** The element that opens the dialog. */
  trigger: ReactElement;
  /** The sessions that would end: every one except this device. */
  sessions: readonly AuthSession[];
}

/**
 * "Sign out of all other sessions?" (Figma "Security — Sign out everywhere dialog"): the approved
 * copy, a tile naming the devices that will be signed out, and a confirm that waits for the server.
 */
export function SignOutOthersDialog({ trigger, sessions }: SignOutOthersDialogProps) {
  const [open, setOpen] = useState(false);
  const revokeOthers = useRevokeOtherAuthSessions();
  const count = sessions.length;

  function confirm() {
    revokeOthers.mutate(undefined, {
      onSuccess: () => {
        toast.success("Signed out your other sessions.");
        setOpen(false);
      },
    });
  }

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (!revokeOthers.isPending) {
          setOpen(next);
        }
      }}
    >
      <DialogTrigger asChild>{trigger}</DialogTrigger>
      <DialogContent hideClose className="max-w-[520px] gap-5 rounded-card p-5 sm:p-7">
        <DialogHeading
          title="Sign out of all other sessions?"
          description="Every other browser and device signed in to your account will be signed out. This one stays signed in."
        />
        <div className="flex flex-col gap-2.5 rounded-tile bg-surface-muted px-4 py-3.5">
          <p className="text-overline text-subtle-foreground uppercase">
            {count} other {count === 1 ? "session" : "sessions"}
          </p>
          <ul aria-label="Sessions that will be signed out" className="flex flex-col gap-2.5">
            {sessions.map((session) => (
              <OtherSession key={session.id} session={session} />
            ))}
          </ul>
        </div>
        <p className="text-xs font-medium text-muted-foreground">
          Personal access tokens keep working. To stop them, revoke them in Settings › Tokens.
        </p>
        <DialogFooter className="gap-2.5">
          <Button
            onClick={() => {
              setOpen(false);
            }}
            disabled={revokeOthers.isPending}
          >
            Cancel
          </Button>
          <Button variant="primary" loading={revokeOthers.isPending} onClick={confirm}>
            Sign out other sessions
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function OtherSession({ session }: { session: AuthSession }) {
  const device = describeUserAgent(session.user_agent);
  const DeviceIcon = device.mobile ? Smartphone : Monitor;

  return (
    <li className="flex items-center gap-2.5">
      <span
        aria-hidden
        className="flex size-7 shrink-0 items-center justify-center rounded-full border border-border bg-surface"
      >
        <DeviceIcon className="size-3.5 text-foreground" strokeWidth={1.75} />
      </span>
      <span className="min-w-0 truncate text-sm font-semibold text-foreground">{device.label}</span>
      <span className="shrink-0 text-xs font-medium text-muted-foreground">
        Last active <RelativeTime iso={session.last_seen_at} />
      </span>
    </li>
  );
}
