import { Link } from "@tanstack/react-router";
import type { ReactNode, Ref } from "react";

import { Logo } from "@/components/logo";
import { LandingScope } from "@/features/landing/landing-scope";
import { DOCS_LINKS, LEGAL_PATHS } from "@/features/landing/links";
import { LEGAL_PAGES_ENABLED } from "@/features/legal/legal-enabled";

import { AuthShowcase } from "./auth-showcase";

import "./auth.css";

const FOOTER_LINK = "auth-tap lp-control rounded-xs hover:text-mono-900";

/** Terms, Privacy policy and Docs; a build without the legal pages links to the docs only. */
function ShellFooter() {
  return (
    <footer className="mt-auto flex w-full max-w-[380px] gap-5 text-[13px] leading-[1.4] text-mono-600 lg:mt-0 lg:max-w-none">
      {LEGAL_PAGES_ENABLED ? (
        <>
          <Link to={LEGAL_PATHS.terms} className={FOOTER_LINK}>
            Terms
          </Link>
          <Link to={LEGAL_PATHS.privacy} className={FOOTER_LINK}>
            Privacy policy
          </Link>
        </>
      ) : null}
      <a href={DOCS_LINKS.home} className={FOOTER_LINK}>
        Docs
      </a>
    </footer>
  );
}

/**
 * The frame of every auth page (Figma "Auth v2"): a white form panel, 600px wide on desktop with
 * a 380px form column, and the black showcase filling the rest and staying in view while the
 * panel scrolls. Below 1024px the showcase is dropped and the form is the page. The colours are
 * the landing's fixed mono palette in both themes.
 */
export function AuthShell({ children }: { children: ReactNode }) {
  return (
    <LandingScope className="auth flex min-h-dvh bg-mono-white">
      <div className="flex min-h-dvh w-full min-w-0 flex-col items-center gap-9 px-5 pt-5 pb-8 lg:w-[600px] lg:shrink-0 lg:justify-between lg:px-12 lg:py-9">
        <div className="w-full max-w-[380px] lg:w-auto">
          <Link
            to="/"
            aria-label="Spanlight home"
            className="inline-flex min-h-11 items-center rounded-md"
          >
            <Logo />
          </Link>
        </div>
        <main className="flex w-full max-w-[380px] flex-col gap-6 lg:gap-7">{children}</main>
        <ShellFooter />
      </div>
      <AuthShowcase className="hidden lg:block" />
    </LandingScope>
  );
}

interface AuthLayoutProps {
  /** The first part of the headline, e.g. "Welcome". */
  title: string;
  /** The words that complete it, e.g. "back."; both parts render as one plain line. */
  accent?: string;
  description?: ReactNode;
  /** Makes the headline focusable, for a page that moves focus to it when its state changes. */
  headingRef?: Ref<HTMLHeadingElement>;
  children: ReactNode;
}

/** An auth page: the shell, its headline and one sentence, then the form or the message. */
export function AuthLayout({ title, accent, description, headingRef, children }: AuthLayoutProps) {
  return (
    <AuthShell>
      <div className="flex flex-col gap-2.5">
        <h1
          ref={headingRef}
          tabIndex={headingRef ? -1 : undefined}
          className="text-4xl leading-[1.04] font-medium tracking-[-0.045em] [overflow-wrap:anywhere] text-mono-900 lg:text-[44px]"
        >
          {accent ? `${title} ${accent}` : title}
        </h1>
        {description ? (
          <p className="text-base leading-normal text-mono-600">{description}</p>
        ) : null}
      </div>
      {children}
    </AuthShell>
  );
}
