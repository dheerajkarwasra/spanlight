import { useState, type ReactElement } from "react";

import { DialogHeading } from "@/components/dialog-heading";
import { Dialog, DialogContent, DialogTrigger } from "@/components/ui/dialog";

import type { ColumnState } from "./config-state";
import { SaveVersionForm, type SavedVersion } from "./save-version-form";

interface SaveVersionDialogProps {
  column: ColumnState;
  letter: string;
  /** The "Save as version" button. */
  trigger: ReactElement;
  onSaved: (saved: SavedVersion) => void;
}

/** Figma "Playground — Save as version". Mounted only while open, so it starts fresh each time. */
export function SaveVersionDialog({ column, letter, trigger, onSaved }: SaveVersionDialogProps) {
  const [open, setOpen] = useState(false);
  const [pending, setPending] = useState(false);

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        // Nothing may close it while the version is being written.
        if (next || !pending) {
          setOpen(next);
        }
      }}
    >
      <DialogTrigger asChild>{trigger}</DialogTrigger>
      {open ? (
        <DialogContent
          hideClose
          className="max-h-[calc(100dvh-2rem)] max-w-[520px] gap-5 overflow-y-auto rounded-dialog p-6 sm:p-7"
        >
          <DialogHeading
            title="Save as new version"
            description={`Creates a version of the prompt from the messages in column ${letter}.`}
            showClose={pending ? "disabled" : true}
          />
          <SaveVersionForm
            column={column}
            letter={letter}
            onPendingChange={setPending}
            onSaved={(saved) => {
              setOpen(false);
              onSaved(saved);
            }}
          />
        </DialogContent>
      ) : null}
    </Dialog>
  );
}
