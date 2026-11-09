import { Link2 } from "lucide-react";
import { useId, useState } from "react";

import { CopyButton } from "@/components/copy-button";
import { SectionCard } from "@/components/section-card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import type { CreatedInvite } from "@/lib/api";
import { formatTimestamp } from "@/lib/format";

import { InviteDialog } from "./invite-dialog";
import { inviteExpiryLabel, roleWithArticle } from "./member-utils";

export function InviteSection() {
  const [invite, setInvite] = useState<CreatedInvite | null>(null);
  return (
    <SectionCard
      title="Invite people"
      description="Create a single-use link and share it with the person you want to add."
      className="gap-4"
    >
      <div>
        <InviteDialog onCreated={setInvite} />
      </div>
      {invite ? <CreatedInviteLink invite={invite} /> : null}
    </SectionCard>
  );
}

function CreatedInviteLink({ invite }: { invite: CreatedInvite }) {
  const linkId = useId();

  return (
    <div className="flex flex-col gap-2.5 rounded-tile bg-surface-muted p-4">
      <div className="flex items-center justify-between gap-3">
        <Label htmlFor={linkId} className="flex items-center gap-1.5 text-sm">
          <Link2 aria-hidden className="size-3.5" />
          Invite link
        </Label>
        <span
          className="text-xs font-medium text-muted-foreground"
          title={formatTimestamp(invite.expires_at) ?? undefined}
        >
          {inviteExpiryLabel(invite.expires_at)}
        </span>
      </div>
      <div className="flex items-center gap-2">
        <Input
          id={linkId}
          readOnly
          value={invite.url}
          className="h-11 flex-1 border-border px-3.5 font-mono text-label"
          onFocus={(event) => {
            event.currentTarget.select();
          }}
        />
        <CopyButton
          value={invite.url}
          label="Copy"
          aria-label="Copy invite link"
          showLabel
          variant="secondary"
          className="h-11 px-4 text-sm shadow-none [&_svg]:size-3.5"
        />
      </div>
      <p className="text-xs font-medium text-muted-foreground">
        {invite.emailed
          ? `Link also sent to ${invite.email}. Works once.`
          : `Anyone with this link can join as ${roleWithArticle(invite.role)}. It works once.`}
      </p>
    </div>
  );
}
