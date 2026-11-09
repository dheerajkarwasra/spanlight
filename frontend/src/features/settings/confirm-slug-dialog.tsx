import { useRef, useState, type ReactElement, type ReactNode } from "react";

import { Callout } from "@/components/callout";
import { DialogHeading } from "@/components/dialog-heading";
import { DisabledReason } from "@/components/disabled-reason";
import { FormField } from "@/components/form-field";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogFooter, DialogTrigger } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import type { OrgDeleteProof } from "@/lib/api/orgs";

import { isConfirmationMismatch, matchesSlug, mismatchMessage } from "./confirm-slug";
import { MemberReauthFields } from "./member-reauth";
import type { useMemberReauth } from "./use-member-reauth";

interface ConfirmSlugDialogProps {
  /** The element that opens the dialog, usually the danger zone's button. */
  trigger: ReactElement;
  /** What has to be typed: the slug of the organization or project, exactly. */
  slug: string;
  copy: { title: string; description: string; confirmLabel: string };
  /**
   * Deletes with the typed slug. The dialog stays open, with a spinner, until it settles and closes
   * on success. Mismatches and proof errors stay inline; other errors appear in the dialog.
   */
  onConfirm: (confirm: string, proof: OrgDeleteProof) => Promise<void>;
  confirmation: ReturnType<typeof useMemberReauth>;
  resource?: "organization" | "project";
  /** What is about to be lost, shown between the description and the field. */
  children?: ReactNode;
}

/**
 * The typed-slug delete confirmation (Figma "Delete organization" and "Delete project" dialogs).
 * The delete button stays disabled until the slug is typed exactly, and the field has the focus
 * when the dialog opens.
 */
export function ConfirmSlugDialog({
  trigger,
  slug,
  copy,
  onConfirm,
  children,
  confirmation,
  resource = "organization",
}: ConfirmSlugDialogProps) {
  const [open, setOpen] = useState(false);
  const [typed, setTyped] = useState("");
  const [pending, setPending] = useState(false);
  const [mismatch, setMismatch] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);
  const matches = matchesSlug(typed, slug);

  function handleOpenChange(next: boolean) {
    if (pending) {
      return;
    }
    setOpen(next);
    if (!next) {
      setTyped("");
      setMismatch(false);
      confirmation.reset();
    }
  }

  async function confirm(proof: OrgDeleteProof) {
    if (!matches || pending) {
      return;
    }
    setPending(true);
    try {
      await onConfirm(typed, proof);
      handleOpenChange(false);
    } catch (error) {
      if (isConfirmationMismatch(error)) {
        setMismatch(true);
      } else {
        confirmation.onError(error);
      }
    } finally {
      setPending(false);
    }
  }

  const deleteButton = (
    <Button
      type="submit"
      variant="danger"
      disabled={!matches || confirmation.reauth}
      loading={pending}
      className="max-sm:w-full"
    >
      {copy.confirmLabel}
    </Button>
  );

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogTrigger asChild>{trigger}</DialogTrigger>
      <DialogContent
        hideClose
        className="max-h-[calc(100dvh-2rem)] max-w-[560px] gap-5 overflow-y-auto rounded-card p-5 sm:p-7"
        onOpenAutoFocus={(event) => {
          // Start at the slug, before any account-owner proof fields.
          event.preventDefault();
          inputRef.current?.focus();
        }}
      >
        <DialogHeading title={copy.title} description={copy.description} />
        {children}
        <form
          noValidate
          className="flex flex-col gap-5"
          onSubmit={(event) => {
            event.preventDefault();
            void confirmation.form.handleSubmit(async (values) => {
              if (!matches || pending) return;
              const proof = confirmation.proof(values);
              if (proof) await confirm(proof);
            })(event);
          }}
        >
          <FormField
            label={
              <>
                Type <code className="font-mono font-normal">{slug}</code> to confirm
              </>
            }
            error={mismatch ? mismatchMessage(slug) : undefined}
          >
            <Input
              ref={inputRef}
              value={typed}
              onChange={(event) => {
                setTyped(event.target.value);
                setMismatch(false);
              }}
              autoComplete="off"
              autoCapitalize="none"
              autoCorrect="off"
              spellCheck={false}
              readOnly={pending}
              className="font-mono text-label"
            />
          </FormField>
          <MemberReauthFields
            confirmation={confirmation}
            pending={pending}
            action={resource === "project" ? "Deleting a project" : "Deleting an organization"}
          />
          {/* The visible error is tied to the field, but a new one is not announced by itself. */}
          <p role="status" className="sr-only">
            {mismatch
              ? mismatchMessage(slug)
              : (confirmation.form.formState.errors.password?.message ??
                confirmation.form.formState.errors.code?.message ??
                "")}
          </p>
          <p className="text-xs font-medium text-muted-foreground">
            {resource === "project"
              ? "If email is set up, the owners are told."
              : "If email is set up, the other owners are told."}
          </p>
          {confirmation.formError ? (
            <Callout tone="danger" role="alert">
              {confirmation.formError}
            </Callout>
          ) : null}
          <DialogFooter className="gap-2.5">
            <Button
              onClick={() => {
                handleOpenChange(false);
              }}
              disabled={pending}
            >
              Cancel
            </Button>
            {confirmation.reauth ? (
              <DisabledReason reason="Sign in again first.">{deleteButton}</DisabledReason>
            ) : matches ? (
              deleteButton
            ) : (
              <DisabledReason reason={`Type ${slug} to confirm.`}>{deleteButton}</DisabledReason>
            )}
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
