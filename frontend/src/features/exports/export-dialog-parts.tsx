import { X, type LucideIcon } from "lucide-react";
import type { ReactNode } from "react";

import { Callout } from "@/components/callout";
import { Button } from "@/components/ui/button";
import { DialogClose, DialogDescription, DialogTitle } from "@/components/ui/dialog";
import { errorMessage } from "@/lib/api";
import { cn } from "@/lib/utils";

import { isExportNotConfigured } from "./export-status";

interface DialogHeadingProps {
  title: string;
  /** Left out in the queued state, where the success tile carries the description. */
  description?: string;
  /** Off while the request is out, so the dialog cannot be closed mid-submit. */
  closeDisabled?: boolean;
}

/** Title, optional description and the round muted close button of the Figma dialog. */
export function DialogHeading({ title, description, closeDisabled = false }: DialogHeadingProps) {
  return (
    <div className="flex items-start justify-between gap-4">
      <div className="flex min-w-0 flex-col gap-1.5">
        <DialogTitle>{title}</DialogTitle>
        {description ? <DialogDescription>{description}</DialogDescription> : null}
      </div>
      <DialogClose asChild>
        <Button
          variant="ghost"
          size="icon-sm"
          aria-label="Close"
          disabled={closeDisabled}
          className="bg-surface-muted"
        >
          <X aria-hidden />
        </Button>
      </DialogClose>
    </div>
  );
}

/**
 * Why Start export failed. `409 NOT_CONFIGURED` is the warning from the Figma frame: the button
 * stays enabled so the person can try again once storage is set up. Anything else is an error.
 */
export function SubmitError({ error }: { error: unknown }) {
  if (isExportNotConfigured(error)) {
    return (
      <Callout tone="warning" role="status" title="Not available on this server">
        Exports need object storage. Ask your administrator to set the S3 settings.
      </Callout>
    );
  }
  return (
    <Callout tone="danger" role="alert" title="Couldn't start the export">
      {errorMessage(error)}
    </Callout>
  );
}

interface SummaryRowProps {
  icon: LucideIcon;
  label: string;
  align?: "start" | "center";
  /** Puts the value under its label on a phone, as the attribution dialog's 375 frame does. */
  stackOnPhone?: boolean;
  children: ReactNode;
}

/** One line of a "What's exported" box: an icon and a muted label, then the value. */
export function SummaryRow({
  icon: Icon,
  label,
  align = "center",
  stackOnPhone = false,
  children,
}: SummaryRowProps) {
  return (
    <div
      className={cn(
        "flex gap-3",
        align === "start" ? "items-start" : "items-center",
        stackOnPhone && "max-sm:flex-col max-sm:items-start max-sm:gap-1",
      )}
    >
      <div className="flex h-[21px] w-24 shrink-0 items-center gap-2 text-sm text-muted-foreground sm:w-[120px]">
        <Icon aria-hidden className="size-4 shrink-0 text-foreground" />
        {label}
      </div>
      <div className="min-w-0 flex-1">{children}</div>
    </div>
  );
}
