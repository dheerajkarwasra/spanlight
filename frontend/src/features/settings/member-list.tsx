import { useRef } from "react";

import { DisabledReason } from "@/components/disabled-reason";
import { ColumnLabels, RowAction, TileList } from "@/components/tile-list";
import { Badge } from "@/components/ui/badge";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import type { Member } from "@/lib/api";
import { formatDate } from "@/lib/format";
import { ROLE_LABELS, ROLES } from "@/lib/permissions";
import { cn } from "@/lib/utils";

import { InitialAvatar } from "./initial-avatar";
import type { MemberRemoval } from "./member-remove-dialog";
import type { MemberRoleChange } from "./member-role-dialog";
import { displayName, isRole } from "./member-utils";

interface MemberListProps {
  members: Member[];
  currentUserId: string;
  canManage: boolean;
  onRoleChange: (change: MemberRoleChange) => void;
  onRemove: (removal: MemberRemoval) => void;
}

/**
 * Figma "Settings/Member row" tiles. On narrow cards (container query) the role, join date and
 * Remove wrap onto a second line under the person.
 */
export function MemberList({
  members,
  currentUserId,
  canManage,
  onRemove,
  onRoleChange,
}: MemberListProps) {
  return (
    <div className="@container flex flex-col gap-3">
      <ColumnLabels className="px-3 @[36rem]:flex">
        <span className="min-w-0 flex-1">Member</span>
        <span className="w-[120px]">Role</span>
        <span className="w-[100px]">Joined</span>
        {canManage ? <span className="w-20" /> : null}
      </ColumnLabels>
      <TileList label="Members">
        {members.map((member) => (
          <MemberRow
            key={member.user.id}
            member={member}
            isSelf={member.user.id === currentUserId}
            canManage={canManage}
            onRemove={onRemove}
            onRoleChange={onRoleChange}
          />
        ))}
      </TileList>
    </div>
  );
}

interface MemberRowProps {
  member: Member;
  isSelf: boolean;
  canManage: boolean;
  onRoleChange: (change: MemberRoleChange) => void;
  onRemove: (removal: MemberRemoval) => void;
}

function MemberRow({ member, isSelf, canManage, onRemove, onRoleChange }: MemberRowProps) {
  const name = displayName(member);

  return (
    <li className="flex flex-wrap items-center gap-x-3 gap-y-2.5 rounded-tile bg-surface-muted px-3 py-2.5 @[36rem]:flex-nowrap">
      <div className="flex min-w-0 basis-full items-center gap-3 @[36rem]:flex-1 @[36rem]:basis-auto">
        <InitialAvatar name={name} seed={member.user.id} size="md" />
        <div className="flex min-w-0 flex-1 flex-col gap-px">
          <span className="flex min-w-0 items-center gap-2">
            <span className="truncate text-sm font-semibold text-foreground">{name}</span>
            {isSelf ? (
              <Badge variant="accent" className="shrink-0">
                You
              </Badge>
            ) : null}
          </span>
          {member.user.name ? (
            <span className="truncate text-xs font-medium text-muted-foreground">
              {member.user.email}
            </span>
          ) : null}
        </div>
      </div>

      <div className="w-[120px] shrink-0">
        {canManage ? (
          <MemberRoleSelect member={member} isSelf={isSelf} onRoleChange={onRoleChange} />
        ) : (
          <span className="text-sm text-muted-foreground">
            <span className="sr-only">Role: </span>
            {ROLE_LABELS[member.role]}
          </span>
        )}
      </div>
      <span className="shrink-0 text-sm text-muted-foreground @[36rem]:w-[100px]">
        <span className="sr-only">Joined </span>
        <time dateTime={member.created_at}>{formatDate(member.created_at)}</time>
      </span>
      {canManage ? (
        <div className="ml-auto flex shrink-0 justify-end @[36rem]:ml-0 @[36rem]:w-20">
          <RemoveMemberAction member={member} isSelf={isSelf} onRemove={onRemove} />
        </div>
      ) : null}
    </li>
  );
}

/** Figma "Settings/Select pill". */
function MemberRoleSelect({
  member,
  isSelf,
  onRoleChange,
}: {
  member: Member;
  isSelf: boolean;
  onRoleChange: (change: MemberRoleChange) => void;
}) {
  const name = displayName(member);
  const trigger = useRef<HTMLButtonElement>(null);

  const select = (
    <Select
      value={member.role}
      disabled={isSelf}
      onValueChange={(next) => {
        if (!isRole(next) || next === member.role) {
          return;
        }
        onRoleChange({ member, role: next, select: trigger.current });
      }}
    >
      <SelectTrigger
        ref={trigger}
        aria-label={`Role for ${name}`}
        className={cn(
          "h-[34px] w-[120px] rounded-full border-border-strong pr-2.5 pl-3.5 font-medium",
          "[&>svg]:size-3.5",
        )}
      >
        <SelectValue />
      </SelectTrigger>
      <SelectContent>
        {ROLES.map((role) => (
          <SelectItem key={role} value={role}>
            {ROLE_LABELS[role]}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  );

  if (isSelf) {
    return (
      <DisabledReason reason="You can't change your own role. Ask another admin or owner.">
        {select}
      </DisabledReason>
    );
  }
  return select;
}

interface RemoveMemberActionProps {
  member: Member;
  isSelf: boolean;
  onRemove: (removal: MemberRemoval) => void;
}

function RemoveMemberAction({ member, isSelf, onRemove }: RemoveMemberActionProps) {
  const name = displayName(member);

  if (isSelf) {
    return (
      <DisabledReason reason="You can't remove yourself. Ask another admin or owner.">
        <RowAction disabled>Remove</RowAction>
      </DisabledReason>
    );
  }

  return (
    <RowAction
      aria-label={`Remove ${name}`}
      onClick={(event) => onRemove({ member, button: event.currentTarget })}
    >
      Remove
    </RowAction>
  );
}
