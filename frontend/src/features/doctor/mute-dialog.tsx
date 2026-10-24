import { useRef, useState } from "react";
import { toast } from "sonner";

import { Callout } from "@/components/callout";
import { DialogHeading } from "@/components/dialog-heading";
import { FormField } from "@/components/form-field";
import { Button } from "@/components/ui/button";
import { Dialog, DialogClose, DialogContent } from "@/components/ui/dialog";
import { Textarea } from "@/components/ui/input";
import type { MuteInput } from "@/lib/api";
import { pluralize } from "@/lib/format";

import { useMuteInsight } from "./doctor-queries";
import { actionErrorMessage } from "./insight-errors";
import { DateField } from "./mute-date-field";
import {
  checkMute,
  MAX_MUTE_DAYS,
  maxMuteLabel,
  REASON_MAX_LENGTH,
  type MuteErrors,
} from "./mute-form";

const MUTE_BODY = "Muted insights keep counting occurrences but never notify.";

type MuteDialogProps = {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** The reason the field opens with, e.g. from a Wrong vote's note. */
  defaultReason?: string;
} & (
  | { insightId: string; onSubmit?: undefined; count?: undefined; onCloseAutoFocus?: undefined }
  /** Bulk mode: the caller mutes the `count` insights and reports per id; it must not throw. */
  | {
      insightId?: undefined;
      onSubmit: (values: MuteInput) => Promise<void>;
      count: number;
      /** Where focus goes on close when the trigger may be gone; `preventDefault` to take it. */
      onCloseAutoFocus?: (event: Event) => void;
    }
);

/** Figma "Doctor — Mute dialog": a date up to 90 days ahead (with presets) and a reason. */
export function MuteDialog({ open, onOpenChange, defaultReason, ...target }: MuteDialogProps) {
  const [pending, setPending] = useState(false);
  // Unused in bulk mode; hooks can't be called conditionally.
  const mute = useMuteInsight(target.insightId ?? "");
  const submit =
    target.onSubmit ??
    (async (values: MuteInput) => {
      await mute.mutateAsync(values);
      toast.success("Muted the insight.");
    });
  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (next || !pending) {
          onOpenChange(next);
        }
      }}
    >
      {/* Mounted only while open, so the dates are measured from when it opens. */}
      {open ? (
        <MuteDialogBody
          count={target.count ?? 1}
          onCloseAutoFocus={target.onCloseAutoFocus}
          onSubmit={submit}
          defaultReason={defaultReason ?? ""}
          onPendingChange={setPending}
          onDone={() => {
            onOpenChange(false);
          }}
        />
      ) : null}
    </Dialog>
  );
}

interface BodyProps {
  count: number;
  onCloseAutoFocus: ((event: Event) => void) | undefined;
  onSubmit: (values: MuteInput) => Promise<void>;
  defaultReason: string;
  onPendingChange: (pending: boolean) => void;
  onDone: () => void;
}

function MuteDialogBody(props: BodyProps) {
  const { count, onSubmit, defaultReason, onPendingChange, onDone } = props;
  const [pending, setPending] = useState(false);
  const [now] = useState(() => new Date());
  const [date, setDate] = useState("");
  const [reason, setReason] = useState(defaultReason);
  const [errors, setErrors] = useState<MuteErrors>({});
  const [failure, setFailure] = useState<string | null>(null);
  const formRef = useRef<HTMLFormElement>(null);

  async function submit() {
    // The end is measured from the moment of the click, not from when the dialog opened.
    const check = checkMute({ date, reason }, new Date());
    setErrors(check.errors);
    if (check.until === null) {
      focusFirstInvalid(formRef.current, check.errors);
      return;
    }
    setFailure(null);
    setPending(true);
    onPendingChange(true);
    try {
      await onSubmit({ until: check.until, reason: reason.trim() });
      onDone();
    } catch (error) {
      setFailure(actionErrorMessage(error));
    } finally {
      setPending(false);
      onPendingChange(false);
    }
  }

  return (
    <DialogContent
      hideClose
      onCloseAutoFocus={props.onCloseAutoFocus}
      className="max-w-[540px] gap-5 rounded-card p-6 sm:p-7"
    >
      <DialogHeading
        title={count === 1 ? "Mute this insight" : `Mute ${pluralize(count, "insight")}`}
        description={MUTE_BODY}
        showClose={pending ? "disabled" : true}
      />
      <form
        ref={formRef}
        noValidate
        className="flex flex-col gap-5"
        onSubmit={(event) => {
          event.preventDefault();
          void submit();
        }}
      >
        <FormField
          label="Mute until"
          error={errors.date}
          hint={`Up to ${String(MAX_MUTE_DAYS)} days from today (${maxMuteLabel(now)}).`}
        >
          <DateField now={now} value={date} onChange={setDate} />
        </FormField>
        <FormField
          label="Reason"
          error={errors.reason}
          hint={`Required. Up to ${String(REASON_MAX_LENGTH)} characters.`}
        >
          <Textarea
            name="reason"
            rows={2}
            value={reason}
            placeholder={`Why are you muting ${count === 1 ? "this insight" : "these insights"}?`}
            onChange={(event) => {
              setReason(event.target.value);
            }}
          />
        </FormField>
        {failure === null ? null : (
          <Callout tone="danger" role="alert">
            {failure}
          </Callout>
        )}
        <div className="flex justify-end gap-2.5">
          <DialogClose asChild>
            <Button disabled={pending}>Cancel</Button>
          </DialogClose>
          <Button type="submit" variant="primary" loading={pending}>
            {count === 1 ? "Mute insight" : "Mute insights"}
          </Button>
        </div>
      </form>
    </DialogContent>
  );
}

/** Moves focus to the first field with an error, so a keyboard user lands on what to fix. */
function focusFirstInvalid(form: HTMLFormElement | null, errors: MuteErrors) {
  const name = errors.date === undefined ? "reason" : "date";
  form?.querySelector<HTMLElement>(`[name="${name}"]`)?.focus();
}
