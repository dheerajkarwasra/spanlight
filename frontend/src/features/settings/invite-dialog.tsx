import { useId, useState } from "react";
import { toast } from "sonner";

import { Callout } from "@/components/callout";
import { DialogHeading } from "@/components/dialog-heading";
import { DisabledReason } from "@/components/disabled-reason";
import { FormField } from "@/components/form-field";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogFooter, DialogTrigger } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import type { CreatedInvite, Role } from "@/lib/api";
import { ROLE_LABELS, ROLES } from "@/lib/permissions";

import { useCreateInvite } from "./member-queries";
import { MemberReauthFields } from "./member-reauth";
import { useMemberReauth } from "./use-member-reauth";
import { isRole, roleHint, roleWithArticle } from "./member-utils";

export function InviteDialog({ onCreated }: { onCreated: (invite: CreatedInvite) => void }) {
  const [open, setOpen] = useState(false);
  const [pending, setPending] = useState(false);
  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (!pending) setOpen(next);
      }}
    >
      <DialogTrigger asChild>
        <Button variant="primary" size="lg">
          Create invite link
        </Button>
      </DialogTrigger>
      <DialogContent
        hideClose
        className="max-h-[calc(100dvh-2rem)] overflow-y-auto rounded-card p-5 sm:p-6"
      >
        <InviteForm
          onPendingChange={setPending}
          onCancel={() => setOpen(false)}
          onCreated={(invite) => {
            onCreated(invite);
            setOpen(false);
          }}
        />
      </DialogContent>
    </Dialog>
  );
}

function InviteForm({
  onPendingChange,
  onCancel,
  onCreated,
}: {
  onPendingChange: (pending: boolean) => void;
  onCancel: () => void;
  onCreated: (invite: CreatedInvite) => void;
}) {
  const confirmation = useMemberReauth();
  const { form, reauth, formError } = confirmation;
  const create = useCreateInvite();
  const [role, setRole] = useState<Role>("member");
  const roleId = useId();
  const hintId = useId();
  const pending = create.isPending;
  const onSubmit = form.handleSubmit(async (values) => {
    if (pending) return;
    const proof = confirmation.proof(values);
    if (!proof) return;
    onPendingChange(true);
    try {
      const created = await create.mutateAsync({
        role,
        ...proof,
        ...(values.invitee.trim() ? { email: values.invitee.trim() } : {}),
      });
      onCreated(created);
      toast.success(`Invite link created for ${roleWithArticle(created.role)}.`);
      create.reset();
    } catch (error) {
      confirmation.onError(error);
    } finally {
      onPendingChange(false);
    }
  });
  const submit = (
    <Button type="submit" variant="primary" loading={pending} disabled={reauth}>
      Create invite link
    </Button>
  );
  return (
    <form noValidate onSubmit={onSubmit} className="flex flex-col gap-5">
      <DialogHeading
        title="Invite someone"
        description="Anyone with the link can join your organization with this role. It works once."
        showClose={pending ? "disabled" : true}
      />
      <div className="flex flex-col gap-2">
        <Label htmlFor={roleId}>Role</Label>
        <Select
          value={role}
          disabled={pending}
          onValueChange={(next) => {
            if (isRole(next)) setRole(next);
          }}
        >
          <SelectTrigger id={roleId} aria-describedby={hintId}>
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {ROLES.map((option) => (
              <SelectItem key={option} value={option}>
                {ROLE_LABELS[option]}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <p id={hintId} className="text-xs font-medium text-muted-foreground">
          {roleHint(role)}
        </p>
      </div>
      <FormField
        label="Email"
        hint="Optional. If email is set up on this server, we also send them the link."
        error={form.formState.errors.invitee?.message}
      >
        <Input
          type="email"
          autoComplete="off"
          data-1p-ignore
          data-lpignore="true"
          readOnly={pending}
          {...form.register("invitee")}
        />
      </FormField>
      <h3 className="text-sm font-semibold">Confirm it's you</h3>
      <MemberReauthFields confirmation={confirmation} pending={pending} action="Inviting someone" />
      {formError ? (
        <Callout tone="danger" role="alert">
          {formError}
        </Callout>
      ) : null}
      <DialogFooter>
        <Button disabled={pending} onClick={onCancel}>
          Cancel
        </Button>
        {reauth ? <DisabledReason reason="Sign in again first.">{submit}</DisabledReason> : submit}
      </DialogFooter>
    </form>
  );
}
