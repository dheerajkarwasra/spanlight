import { Link } from "@tanstack/react-router";
import { Menu } from "lucide-react";
import { useRef, useState } from "react";

import { Logo } from "@/components/logo";
import { GithubMark } from "@/features/auth";
import { Button } from "@/components/ui/button";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetTitle,
  SheetTrigger,
} from "@/components/ui/sheet";

import { DOCS_LINKS, EXTERNAL_LINKS, NAV_LINKS } from "./links";

interface LandingNavProps {
  onTryDemo: () => void;
  demoPending: boolean;
}

/**
 * Floating pill navigation (Figma "Nav bar"): logo, section links, GitHub and the sign-in / demo
 * actions on desktop; logo and a menu button that opens a sheet on smaller screens.
 */
export function LandingNav({ onTryDemo, demoPending }: LandingNavProps) {
  return (
    <header className="relative px-4 pt-4 md:px-8 lg:pt-6">
      <div className="mx-auto flex max-w-[1200px] items-center justify-between rounded-full border border-border bg-surface py-2 pr-2 pl-3.5 lg:p-2.5">
        <div className="flex lg:flex-1 lg:pl-3">
          <Link to="/" aria-label="Spanlight home" className="rounded-md">
            <Logo />
          </Link>
        </div>

        <nav aria-label="Main" className="hidden lg:block">
          <ul className="flex items-center gap-6 xl:gap-7">
            {NAV_LINKS.map((link) => (
              <li key={link.label}>
                <a
                  href={link.href}
                  className="rounded-sm text-sm font-medium whitespace-nowrap text-muted-foreground transition-colors hover:text-foreground"
                >
                  {link.label}
                </a>
              </li>
            ))}
          </ul>
        </nav>

        <div className="hidden flex-1 items-center justify-end gap-1.5 lg:flex">
          <Button asChild variant="ghost" size="icon">
            <a href={EXTERNAL_LINKS.github} aria-label="Spanlight on GitHub">
              <GithubMark className="size-[18px]" />
            </a>
          </Button>
          <Button asChild variant="ghost">
            <Link to="/login">Sign in</Link>
          </Button>
          <Button variant="highlight" loading={demoPending} onClick={onTryDemo}>
            Try the live demo
          </Button>
        </div>

        <MobileMenu onTryDemo={onTryDemo} demoPending={demoPending} />
      </div>
    </header>
  );
}

function MobileMenu({ onTryDemo, demoPending }: LandingNavProps) {
  const [open, setOpen] = useState(false);
  // Following a section link closes the sheet; focus then stays with the section instead of
  // returning to the menu button (which would scroll the page back to the top).
  const followedLinkRef = useRef(false);

  return (
    <Sheet open={open} onOpenChange={setOpen}>
      <SheetTrigger asChild>
        <Button
          variant="primary"
          size="icon"
          aria-label="Open menu"
          className="lg:hidden [&_svg]:size-[18px]"
        >
          <Menu aria-hidden />
        </Button>
      </SheetTrigger>
      <SheetContent
        side="right"
        className="gap-8 p-6"
        onCloseAutoFocus={(event) => {
          if (followedLinkRef.current) {
            event.preventDefault();
            followedLinkRef.current = false;
          }
        }}
      >
        <div className="flex flex-col gap-1.5 pr-10">
          <Logo />
          <SheetTitle className="sr-only">Menu</SheetTitle>
          <SheetDescription className="sr-only">
            Jump to a section, sign in or try the live demo.
          </SheetDescription>
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
                  className="flex h-12 items-center rounded-lg px-3 text-lg font-semibold text-foreground transition-colors hover:bg-surface-hover"
                >
                  {link.label}
                </a>
              </li>
            ))}
            <li>
              <a
                href={EXTERNAL_LINKS.github}
                className="flex h-12 items-center gap-2.5 rounded-lg px-3 text-lg font-semibold text-foreground transition-colors hover:bg-surface-hover"
              >
                <GithubMark className="size-5" />
                GitHub
              </a>
            </li>
          </ul>
        </nav>

        <div className="mt-auto flex flex-col gap-2.5">
          <Button variant="primary" size="lg" loading={demoPending} onClick={onTryDemo}>
            Try the live demo
          </Button>
          <Button asChild size="lg" className="shadow-none">
            <a href={DOCS_LINKS.selfHosting}>Self-host Spanlight</a>
          </Button>
          <Button asChild variant="ghost" size="lg">
            <Link to="/login">Sign in</Link>
          </Button>
        </div>
      </SheetContent>
    </Sheet>
  );
}
