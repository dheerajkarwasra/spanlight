import { Link } from "@tanstack/react-router";
import {
  ArrowUpRight,
  BookOpen,
  Cable,
  Keyboard,
  ListChecks,
  LogOut,
  UserRound,
} from "lucide-react";
import { useState, type Ref } from "react";

import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { useMe } from "@/features/auth";
import { useCanRestoreChecklist, useSetActivationChecklist } from "@/features/onboarding";
import { docsLink } from "@/lib/docs-links";
import { ROLE_LABELS } from "@/lib/permissions";
import { cn } from "@/lib/utils";

import { useCurrentOrg, useCurrentRole, useProjectParams } from "./project-context";
import { useSignOut } from "./use-sign-out";

interface UserMenuProps {
  /** `tile` (the full rail): avatar, name, "Role · Organization". `avatar` (the icon rail): just the avatar. */
  variant?: "tile" | "avatar";
  /** Opens the keyboard shortcuts help; the item shows only when given. */
  onOpenShortcuts?: () => void;
  /** The menu's trigger, which takes focus when the setup checklist above it is hidden. */
  ref?: Ref<HTMLButtonElement>;
}

/** The account menu at the bottom of the rail, also the steady home of help and setup links. */
export function UserMenu({ variant = "tile", onOpenShortcuts, ref }: UserMenuProps) {
  const { user } = useMe();
  const [open, setOpen] = useState(false);
  const canRestoreChecklist = useCanRestoreChecklist(open);
  const setChecklist = useSetActivationChecklist();
  const params = useProjectParams();
  const role = useCurrentRole();
  const org = useCurrentOrg();
  const signOut = useSignOut();
  const displayName = user.name || user.email;
  const context = [role ? ROLE_LABELS[role] : null, org?.name].filter(Boolean).join(" · ");

  return (
    <DropdownMenu open={open} onOpenChange={setOpen}>
      <DropdownMenuTrigger
        ref={ref}
        className={cn(
          "shrink-0 transition-colors duration-200 focus-visible:outline-lime",
          variant === "tile"
            ? "flex w-full items-center gap-3 rounded-2xl bg-rail-tile px-3 py-2.5 text-left hover:bg-rail-tile-hover data-[state=open]:bg-rail-tile-hover"
            : "rounded-full",
        )}
        aria-label={`${[displayName, context].filter(Boolean).join(", ")}. Account menu`}
      >
        <span
          aria-hidden
          className={cn(
            "block shrink-0 rounded-full bg-linear-to-r from-avatar-from to-avatar-to",
            variant === "tile" ? "size-9" : "size-10",
          )}
        />
        {variant === "tile" ? (
          <span className="flex min-w-0 flex-1 flex-col">
            <span className="truncate text-sm font-semibold text-rail-foreground">
              {displayName}
            </span>
            {context ? (
              <span className="truncate text-xs font-medium text-rail-subtle-foreground">
                {context}
              </span>
            ) : null}
          </span>
        ) : null}
      </DropdownMenuTrigger>
      <DropdownMenuContent
        align={variant === "tile" ? "start" : "end"}
        side={variant === "tile" ? "top" : "right"}
        className="w-60"
      >
        <DropdownMenuLabel className="truncate tracking-normal normal-case">
          {user.email}
        </DropdownMenuLabel>
        <DropdownMenuSeparator />
        <DropdownMenuItem asChild>
          <Link to="/$orgId/$projectId/settings/account" params={params}>
            <UserRound aria-hidden />
            Account settings
          </Link>
        </DropdownMenuItem>
        <DropdownMenuItem asChild>
          <a href={docsLink()} target="_blank" rel="noreferrer">
            <BookOpen aria-hidden />
            Docs
            <ArrowUpRight aria-hidden className="ml-auto" />
            <span className="sr-only">(opens in a new tab)</span>
          </a>
        </DropdownMenuItem>
        {onOpenShortcuts ? (
          <DropdownMenuItem onSelect={onOpenShortcuts} aria-keyshortcuts="?">
            <Keyboard aria-hidden />
            Keyboard shortcuts
          </DropdownMenuItem>
        ) : null}
        <DropdownMenuItem asChild>
          <Link to="/onboarding" search={{ org: params.orgId, project: params.projectId }}>
            <Cable aria-hidden />
            Connect your app
          </Link>
        </DropdownMenuItem>
        {canRestoreChecklist ? (
          <DropdownMenuItem
            onSelect={() => {
              setChecklist.mutate(false);
            }}
          >
            <ListChecks aria-hidden />
            Show setup checklist
          </DropdownMenuItem>
        ) : null}
        <DropdownMenuSeparator />
        <DropdownMenuItem
          onSelect={() => {
            signOut.mutate();
          }}
        >
          <LogOut aria-hidden />
          Sign out
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
