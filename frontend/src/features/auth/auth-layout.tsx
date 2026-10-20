import { Link } from "@tanstack/react-router";
import type { ReactNode } from "react";

import { Logo } from "@/components/logo";
import { MeshBackdrop } from "@/components/mesh-backdrop";
import { ThemeToggle } from "@/components/theme-toggle";
import { cn } from "@/lib/utils";

import { AuthShowcase } from "./auth-showcase";

function AuthHeader({ className }: { className?: string }) {
  return (
    <header className={cn("relative flex items-center justify-between", className)}>
      <Link to="/" aria-label="Spanlight home" className="rounded-md">
        <Logo />
      </Link>
      <ThemeToggle />
    </header>
  );
}

interface AuthLayoutProps {
  /** The sans part of the headline, e.g. "Welcome". */
  title: string;
  /** The serif-italic accent that follows it, e.g. "back." */
  accent?: string;
  description?: ReactNode;
  /** A status tile above the headline (see `AuthStatusTile`). Phones then get a smaller headline. */
  status?: ReactNode;
  children: ReactNode;
}

/**
 * Sign in, sign up and the account flows around them (Figma "Sign in" and "Account flows"): the
 * form column on the mesh canvas, and on desktop the ink showcase panel with one drawn trace.
 * Phones get the form only.
 */
export function AuthLayout({ title, accent, description, status, children }: AuthLayoutProps) {
  return (
    <div className="relative flex min-h-dvh overflow-clip bg-canvas lg:p-3">
      <MeshBackdrop className="-top-[190px] right-auto -left-[300px] sm:-top-[170px] sm:-left-[220px]" />

      <div className="relative flex min-w-0 flex-1 flex-col px-6 pt-5 pb-10 lg:px-9 lg:pt-6">
        <AuthHeader />
        <main className="mx-auto flex w-full max-w-[400px] flex-1 flex-col justify-center gap-6 pt-14 lg:py-10">
          {status}
          <div className="flex flex-col gap-3">
            <h1
              className={cn(
                "text-display text-foreground",
                status &&
                  "text-[2rem] leading-[1.04] font-extrabold tracking-[-0.04em] sm:text-display",
              )}
            >
              {title}
              {accent ? (
                <>
                  {" "}
                  <span
                    className={cn(
                      "font-serif-accent text-serif text-accent",
                      status && "text-[2.25rem] leading-none sm:text-serif",
                    )}
                  >
                    {accent}
                  </span>
                </>
              ) : null}
            </h1>
            {description ? (
              <p className="text-sm text-muted-foreground sm:text-lg">{description}</p>
            ) : null}
          </div>
          {children}
        </main>
      </div>

      <AuthShowcase className="hidden min-h-[calc(100dvh-1.5rem)] flex-1 lg:flex" />
    </div>
  );
}

interface AuthCenteredLayoutProps {
  children: ReactNode;
}

/** A single card centred on the mesh canvas (Figma "Invite — Desktop"). */
export function AuthCenteredLayout({ children }: AuthCenteredLayoutProps) {
  return (
    <div className="relative flex min-h-dvh flex-col overflow-clip bg-canvas px-4 pt-5 pb-10 sm:px-12 sm:pt-9">
      <MeshBackdrop className="-top-[180px] right-auto -left-[160px]" />
      <MeshBackdrop className="top-auto -right-[180px] -bottom-[140px]" />
      <AuthHeader />
      <main className="relative flex flex-1 items-center justify-center py-12">{children}</main>
    </div>
  );
}
