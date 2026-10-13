import { Link } from "@tanstack/react-router";
import { ArrowLeft, ChevronDown, LogOut } from "lucide-react";
import type { ReactNode } from "react";

import { Logo } from "@/components/logo";
import { MeshBackdrop } from "@/components/mesh-backdrop";
import { ThemeToggle } from "@/components/theme-toggle";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { useMe } from "@/features/auth";

import { useSignOut } from "./use-sign-out";

interface AccountShellProps {
  children: ReactNode;
}

/**
 * The bare frame for account pages that belong to no project (`/account/*`): the logo, a way back
 * and the account menu with sign out. It exists for someone an organization has locked out until
 * they turn on two-factor authentication, who may have no project they can open, so the project
 * shell and its rail can't be shown.
 */
export function AccountShell({ children }: AccountShellProps) {
  return (
    <div className="relative min-h-dvh overflow-clip bg-background">
      <a
        href="#main"
        className="sr-only z-50 rounded-full bg-surface px-4 py-2 text-sm font-semibold shadow-lg focus:not-sr-only focus:fixed focus:top-3 focus:left-3"
      >
        Skip to content
      </a>
      <MeshBackdrop className="-top-[120px] -left-[200px] lg:top-0 lg:-right-4 lg:left-auto" />

      <header className="relative mx-auto flex w-full max-w-[884px] items-center justify-between gap-3 px-4 pt-4 sm:px-6 sm:pt-6">
        <div className="flex min-w-0 items-center gap-3 sm:gap-5">
          <Link to="/" className="rounded-full">
            <Logo />
          </Link>
          {/*
            Below 640 px the label would sit under the theme toggle (T7-05): the link becomes an
            icon circle, and the label stays its accessible name.
          */}
          <Button asChild variant="ghost" size="sm" className="max-sm:size-10 max-sm:px-0">
            <Link to="/" title="Back to app">
              <ArrowLeft aria-hidden />
              <span className="max-sm:sr-only">Back to app</span>
            </Link>
          </Button>
        </div>
        <div className="flex shrink-0 items-center gap-2">
          <ThemeToggle />
          <AccountMenu />
        </div>
      </header>

      <main
        id="main"
        tabIndex={-1}
        className="relative mx-auto flex w-full max-w-[884px] min-w-0 flex-col px-4 pt-8 pb-16 outline-none sm:px-6"
      >
        {children}
      </main>
    </div>
  );
}

/** The signed-in person's menu: their address, a way back, and sign out. */
function AccountMenu() {
  const { user } = useMe();
  const signOut = useSignOut();
  const displayName = user.name || user.email;

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button aria-label={`${displayName}. Account menu`}>
          <span
            aria-hidden
            className="size-6 shrink-0 rounded-full bg-linear-to-r from-avatar-from to-avatar-to"
          />
          <span className="hidden max-w-32 truncate sm:inline">{displayName}</span>
          <ChevronDown aria-hidden className="text-muted-foreground" />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="w-60">
        <DropdownMenuLabel className="truncate tracking-normal normal-case">
          {user.email}
        </DropdownMenuLabel>
        <DropdownMenuSeparator />
        <DropdownMenuItem asChild>
          <Link to="/">
            <ArrowLeft aria-hidden />
            Back to app
          </Link>
        </DropdownMenuItem>
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
