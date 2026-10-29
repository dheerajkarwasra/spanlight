import type { ReactNode } from "react";

import { Logo } from "@/components/logo";
import { ThemeToggle } from "@/components/theme-toggle";

interface OnboardingLayoutProps {
  /** The step pills, centred in the header on wide screens and on their own row on phones. */
  stepper?: ReactNode;
  /** Optional link back into the app, e.g. "Skip to dashboard". Sits left of the theme toggle. */
  exitLink?: ReactNode;
  children: ReactNode;
}

/**
 * A focused, full-page layout outside the app shell (Figma "Onboarding 1–3"): a neutral canvas,
 * a header with the logo, the step pills and the theme toggle, then a 720px content column.
 */
export function OnboardingLayout({ stepper, exitLink, children }: OnboardingLayoutProps) {
  return (
    <div className="relative flex min-h-dvh flex-col gap-8 overflow-clip bg-canvas px-4 pt-5 pb-12 sm:px-12 sm:pt-9 lg:gap-16">
      <header className="relative grid grid-cols-[1fr_auto] items-center gap-x-3 gap-y-5 lg:grid-cols-[1fr_auto_1fr]">
        <Logo />
        {stepper ? (
          <div className="col-span-2 row-start-2 flex justify-center lg:col-span-1 lg:col-start-2 lg:row-start-1">
            {stepper}
          </div>
        ) : null}
        <div className="flex items-center justify-end gap-1 lg:col-start-3">
          {exitLink}
          <ThemeToggle />
        </div>
      </header>

      <main className="relative mx-auto flex w-full max-w-[720px] min-w-0 flex-col">
        {children}
      </main>
    </div>
  );
}
