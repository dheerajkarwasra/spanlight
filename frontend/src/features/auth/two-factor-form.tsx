import { useId, type RefObject } from "react";

import { Label } from "@/components/ui/label";
import { cn } from "@/lib/utils";

import { AUTH_LABEL, AUTH_LINK, Button, FieldError, Input } from "./auth-controls";
import { CodeInput } from "./code-input";
import { failureMessage } from "./two-factor-flow";
import type { TwoFactorFlow } from "./use-two-factor-flow";

interface TwoFactorFormProps {
  flow: TwoFactorFlow;
  /** The id of the visible instruction above the form, which is the field's label. */
  instructionId: string;
  /** The field's element, which the flow focuses and selects. */
  inputRef: RefObject<HTMLInputElement | null>;
}

/**
 * The code field, Verify, and the switch between the authenticator code and a recovery code. The
 * app's code is one input drawn as six cells; a recovery code is an ordinary text field.
 */
export function TwoFactorForm({ flow, instructionId, inputRef }: TwoFactorFormProps) {
  const errorId = useId();
  const recoveryId = useId();
  const message = flow.failure ? failureMessage(flow.failure, flow.mode) : undefined;

  return (
    <form
      className="flex flex-col gap-6"
      noValidate
      onSubmit={(event) => {
        event.preventDefault();
        flow.submit();
      }}
    >
      {flow.mode === "code" ? (
        <div className="flex flex-col gap-2.5">
          <CodeInput
            value={flow.code}
            onChange={flow.setCode}
            invalid={flow.failure?.kind === "wrong-code"}
            input={{
              ref: inputRef,
              "aria-labelledby": instructionId,
              "aria-describedby": message ? errorId : undefined,
            }}
          />
          {message ? (
            <FieldError id={errorId} role="alert">
              {message}
            </FieldError>
          ) : null}
        </div>
      ) : (
        <div className="flex flex-col gap-2">
          <Label htmlFor={recoveryId} className={AUTH_LABEL}>
            Recovery code
          </Label>
          <Input
            id={recoveryId}
            ref={inputRef}
            value={flow.code}
            onChange={(event) => {
              flow.setCode(event.target.value);
            }}
            autoComplete="off"
            autoCapitalize="none"
            autoCorrect="off"
            spellCheck={false}
            aria-invalid={message ? true : undefined}
            aria-describedby={message ? errorId : undefined}
            className="font-mono tracking-[0.04em]"
          />
          {message ? (
            <FieldError id={errorId} role="alert">
              {message}
            </FieldError>
          ) : null}
        </div>
      )}

      <div className="flex flex-col gap-6">
        <Button
          type="submit"
          variant="primary"
          size="lg"
          loading={flow.pending}
          disabled={!flow.canSubmit}
        >
          Verify
        </Button>
        <Button
          variant="link"
          className={cn(AUTH_LINK, "min-h-11 self-start lg:min-h-0")}
          onClick={flow.switchMode}
        >
          {flow.mode === "code"
            ? "Use a recovery code instead"
            : "Use your authenticator app instead"}
        </Button>
      </div>
    </form>
  );
}
