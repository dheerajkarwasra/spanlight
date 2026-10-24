import { Info } from "lucide-react";

import { DialogHeading } from "@/components/dialog-heading";
import { Button } from "@/components/ui/button";
import { Dialog, DialogClose, DialogContent } from "@/components/ui/dialog";

import { KeyChip } from "./key-chip";
import { SHORTCUT_ROWS } from "./shortcuts";

interface ShortcutHelpProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

/** Figma "Annotate — shortcut help overlay": the work-mode keymap. `?` or `Esc` closes it. */
export function ShortcutHelp({ open, onOpenChange }: ShortcutHelpProps) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent hideClose className="max-w-[520px] gap-[18px] rounded-card p-6 sm:p-7">
        <DialogHeading
          title="Keyboard shortcuts"
          description="Score without leaving the keyboard. Press ? or Esc to close."
        />
        <ul className="flex flex-col gap-1.5" aria-label="Shortcuts">
          {SHORTCUT_ROWS.map((row) => (
            <li
              key={row.label}
              className="flex items-center gap-3 rounded-input bg-surface-muted px-3.5 py-2.5"
            >
              <span className="flex w-[110px] shrink-0 items-center gap-1">
                <KeyChip>{row.keys[0] ?? ""}</KeyChip>
                {row.range ? (
                  <>
                    <span aria-label="to" className="text-label text-muted-foreground">
                      –
                    </span>
                    <KeyChip>{row.keys[1] ?? ""}</KeyChip>
                  </>
                ) : null}
              </span>
              <span className="min-w-0 flex-1 text-sm font-medium text-foreground">
                {row.label}
              </span>
            </li>
          ))}
        </ul>
        <p className="flex items-center gap-2 rounded-input bg-warning-subtle px-3.5 py-3 text-label font-medium text-foreground">
          <Info aria-hidden className="size-3.5 shrink-0 text-warning" />
          Shortcuts are ignored while you type in the comment field. Release is a button; it has no
          key.
        </p>
        <p className="flex items-center gap-2 text-label font-medium text-muted-foreground">
          <Info aria-hidden className="size-3.5 shrink-0" />
          Evaluator scores stay hidden until you submit.
        </p>
        <div className="flex justify-end">
          <DialogClose asChild>
            <Button variant="primary">Close</Button>
          </DialogClose>
        </div>
      </DialogContent>
    </Dialog>
  );
}
