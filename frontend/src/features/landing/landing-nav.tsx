import { Link } from "@tanstack/react-router";
import { Menu, X } from "lucide-react";
import { useRef, useState } from "react";

import { Logo } from "@/components/logo";
import {
  Sheet,
  SheetClose,
  SheetContent,
  SheetDescription,
  SheetTitle,
  SheetTrigger,
} from "@/components/ui/sheet";
import { GithubMark } from "@/features/auth";
import { cn } from "@/lib/utils";

import { BUTTON_PRIMARY, BUTTON_SECONDARY } from "./button-styles";
import { type DemoActions } from "./landing-parts";
import { LandingScope } from "./landing-scope";
import { DOCS_LINKS, EXTERNAL_LINKS, NAV_LINKS } from "./links";

interface LandingNavProps extends DemoActions {
  /** The landing page keeps the bar in view while scrolling; the legal pages let it scroll away. */
  sticky?: boolean;
}

const NAV_LINK = "lp-control rounded-sm text-sm leading-[1.4] font-medium whitespace-nowrap";

/**
 * The black navigation bar (Figma "Nav"): logo, section links, GitHub and the sign-in and sign-up
 * actions on desktop; logo, "Start free" and a menu button that opens a sheet on smaller screens.
 * It carries the landing colour scope itself, so the legal pages get the same bar.
 */
export function LandingNav({ onTryDemo, demoPending, sticky = false }: LandingNavProps) {
  return (
    <LandingScope
      as="header"
      className={cn(
        "lp-dark z-40 border-b border-mono-800 bg-mono-black px-5 xl:px-6",
        sticky && "sticky top-0",
      )}
    >
      <div className="mx-auto flex h-[60px] max-w-[640px] items-center justify-between xl:h-[72px] xl:max-w-[1200px]">
        <div className="flex xl:w-[300px]">
          <Link
            to="/"
            aria-label="Spanlight home"
            className="lp-control flex min-h-11 items-center rounded-md"
          >
            <Logo tone="on-dark" />
          </Link>
        </div>

        <nav aria-label="Main" className="hidden xl:block">
          <ul className="flex items-center gap-9">
            {NAV_LINKS.map((link) => (
              <li key={link.label}>
                <a href={link.href} className={cn(NAV_LINK, "text-mono-400 hover:text-mono-white")}>
                  {link.label}
                </a>
              </li>
            ))}
          </ul>
        </nav>

        <div className="hidden w-[300px] items-center justify-end gap-5 xl:flex">
          <a
            href={EXTERNAL_LINKS.github}
            aria-label="Spanlight on GitHub"
            className="lp-control rounded-full text-mono-white hover:text-mono-400"
          >
            <GithubMark className="size-5" />
          </a>
          <Link to="/login" className={cn(NAV_LINK, "text-mono-white hover:text-mono-400")}>
            Sign in
          </Link>
          <Link to="/signup" className={cn(BUTTON_PRIMARY, "min-h-0 px-4 py-[9px]")}>
            Start free
          </Link>
        </div>

        <div className="flex items-center gap-1 xl:hidden">
          <Link to="/signup" className={cn(BUTTON_PRIMARY, "px-4 py-[9px]")}>
            Start free
          </Link>
          <MobileMenu onTryDemo={onTryDemo} demoPending={demoPending} />
        </div>
      </div>
    </LandingScope>
  );
}

const SHEET_LINK =
  "lp-control flex h-12 items-center gap-2.5 rounded-lg px-3 text-lg font-medium text-mono-white hover:bg-mono-900";

function MobileMenu({ onTryDemo, demoPending }: DemoActions) {
  const [open, setOpen] = useState(false);
  // Following a section link closes the sheet; focus then stays with the section instead of
  // returning to the menu button (which would scroll the page back to the top).
  const followedLinkRef = useRef(false);

  return (
    <Sheet open={open} onOpenChange={setOpen}>
      <SheetTrigger asChild>
        <button
          type="button"
          aria-label="Open menu"
          className="lp-control flex size-11 items-center justify-center rounded-full text-mono-white"
        >
          <Menu aria-hidden className="size-6" />
        </button>
      </SheetTrigger>
      {/* The sheet is portalled out of the header, so it carries the landing scope itself. */}
      <SheetContent
        side="right"
        hideClose
        className="landing lp-dark gap-8 rounded-[14px] border-mono-800 bg-mono-black p-6 text-mono-white shadow-none"
        onCloseAutoFocus={(event) => {
          if (followedLinkRef.current) {
            event.preventDefault();
            followedLinkRef.current = false;
          }
        }}
      >
        <div className="flex items-center justify-between">
          <Logo tone="on-dark" />
          <SheetTitle className="sr-only">Menu</SheetTitle>
          <SheetDescription className="sr-only">
            Jump to a section, start free, try the live demo or sign in.
          </SheetDescription>
          <SheetClose
            aria-label="Close"
            className="lp-control flex size-11 items-center justify-center rounded-full text-mono-white hover:bg-mono-900"
          >
            <X aria-hidden className="size-5" />
          </SheetClose>
        </div>

        <nav aria-label="Main">
          <ul className="flex flex-col gap-1">
            {NAV_LINKS.map((link) => (
              <li key={link.label}>
                <a
                  href={link.href}
                  onClick={() => {
                    followedLinkRef.current = true;
                    setOpen(false);
                  }}
                  className={SHEET_LINK}
                >
                  {link.label}
                </a>
              </li>
            ))}
            <li>
              <a href={EXTERNAL_LINKS.github} className={SHEET_LINK}>
                <GithubMark className="size-5" />
                GitHub
              </a>
            </li>
          </ul>
        </nav>

        <div className="mt-auto flex flex-col gap-2.5">
          <Link to="/signup" className={BUTTON_PRIMARY}>
            Start free
          </Link>
          <button
            type="button"
            onClick={onTryDemo}
            disabled={demoPending}
            aria-busy={demoPending || undefined}
            className={BUTTON_SECONDARY}
          >
            Try the live demo
          </button>
          <a href={DOCS_LINKS.selfHosting} className={cn(SHEET_LINK, "justify-center text-[15px]")}>
            Self-host Spanlight
          </a>
          <Link to="/login" className={cn(SHEET_LINK, "justify-center text-[15px]")}>
            Sign in
          </Link>
        </div>
      </SheetContent>
    </Sheet>
  );
}
