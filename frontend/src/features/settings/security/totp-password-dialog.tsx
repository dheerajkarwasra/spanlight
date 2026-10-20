import { useState, type SyntheticEvent } from "react";

import { Callout } from "@/components/callout";
import { DialogHeading } from "@/components/dialog-heading";
import { FormField } from "@/components/form-field";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogFooter } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { securityApi, type TotpSetup } from "@/lib/api";
import { useUncachedAction } from "@/lib/use-uncached-action";

import { NOT_AVAILABLE_TITLE, RATE_LIMITED_MESSAGE, classifyTotpError } from "./totp-flow";

interface TotpPasswordDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** The password was right: the secret to show in the setup wizard. */
  onSetup: (setup: TotpSetup) => void;
  /** Turned on in another tab since this page loaded. */
  onAlreadyEnabled: () => void;
}

/**
 * Setting up two-factor authentication asks for the current password first, so a stolen session
 * cookie alone cannot put a second factor between the owner and their account.
 */
export function TotpPasswordDialog({ open, onOpenChange, ...rest }: TotpPasswordDialogProps) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      {/* Mounted only while open, so closing it drops the password. */}
      {open ? <TotpPasswordBody onClose={() => onOpenChange(false)} {...rest} /> : null}
    </Dialog>
  );
}

type Problem =
  | { kind: "field"; message: string }
  | { kind: "unavailable"; message: string }
  | { kind: "rate-limited" };

function TotpPasswordBody({
  onClose,
  onSetup,
  onAlreadyEnabled,
}: Omit<TotpPasswordDialogProps, "open" | "onOpenChange"> & { onClose: () => void }) {
  const start = useUncachedAction(securityApi.totpSetup);
  const [password, setPassword] = useState("");
  const [problem, setProblem] = useState<Problem | null>(null);
  const rateLimited = problem?.kind === "rate-limited";

  async function handleSubmit(event: SyntheticEvent) {
    event.preventDefault();
    if (password === "" || start.pending || rateLimited) {
      return;
    }
    setProblem(null);
    const result = await start.run(password);
    if (result.ok) {
      onClose();
      onSetup(result.value);
      return;
    }
    const failure = classifyTotpError(result.error);
    switch (failure.kind) {
      case "invalid-password":
        setProblem({ kind: "field", message: "That password is not correct." });
        return;
      case "rate-limited":
        setProblem({ kind: "rate-limited" });
        return;
      case "already-enabled":
        onClose();
        onAlreadyEnabled();
        return;
      case "not-configured":
        setProblem({ kind: "unavailable", message: failure.message });
        return;
      default:
        setProblem({
          kind: "field",
          message: failure.kind === "other" ? failure.message : "That didn't work. Try again.",
        });
    }
  }

  const fieldError = problem?.kind === "field" ? problem.message : undefined;

  return (
    <DialogContent hideClose className="max-w-[560px] gap-0 rounded-card p-5 sm:p-7">
      <form
        onSubmit={(event) => {
          void handleSubmit(event);
        }}
        noValidate
        className="flex flex-col gap-5"
      >
        <DialogHeading
          showClose={start.pending ? "disabled" : true}
          title="Confirm your password"
          description="Enter your current password to set up two-factor authentication."
        />
        {problem?.kind === "unavailable" ? (
          <Callout tone="warning" title={NOT_AVAILABLE_TITLE} role="alert">
            {problem.message}
          </Callout>
        ) : null}
        <FormField label="Password" error={rateLimited ? RATE_LIMITED_MESSAGE : fieldError}>
          <Input
            type="password"
            value={password}
            onChange={(event) => {
              setPassword(event.target.value);
              setProblem((current) => (current?.kind === "rate-limited" ? current : null));
            }}
            autoComplete="current-password"
            autoFocus
          />
        </FormField>
        <DialogFooter className="gap-2.5">
          <Button onClick={onClose} disabled={start.pending}>
            Cancel
          </Button>
          <Button
            type="submit"
            variant="primary"
            loading={start.pending}
            disabled={password === "" || rateLimited}
          >
            Continue
          </Button>
        </DialogFooter>
      </form>
    </DialogContent>
  );
}
