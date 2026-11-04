import type { ReactNode } from "react";

import { LandingFooter } from "@/features/landing/landing-footer";
import { LandingNav } from "@/features/landing/landing-nav";
import { useStartDemo } from "@/features/landing/use-start-demo";

interface LegalPageProps {
  title: string;
  /** ISO date the text was finished, shown as "Last updated". */
  version: string;
  /** Section links, for documents with more than three sections. */
  contents?: { id: string; title: string }[];
  children: ReactNode;
}

/**
 * Public layout for the legal documents (Figma "Legal page", 846:99 / 846:329): the landing nav
 * and footer around an optional contents card and a readable article card.
 */
export function LegalPage({ title, version, contents, children }: LegalPageProps) {
  const demo = useStartDemo();
  const demoActions = {
    onTryDemo: () => {
      demo.mutate();
    },
    demoPending: demo.isPending,
  };

  return (
    <div className="relative min-h-dvh overflow-x-clip bg-background">
      <LandingNav {...demoActions} />
      <main className="px-4 pt-6 pb-12 md:px-8 lg:pt-14 lg:pb-24">
        <div className="mx-auto flex max-w-[1104px] flex-col gap-4 lg:flex-row lg:items-start lg:justify-center lg:gap-10">
          {contents && (
            <nav
              aria-label="Contents"
              className="flex flex-col gap-2 rounded-tile border border-border bg-surface p-4 lg:sticky lg:top-6 lg:w-[264px] lg:shrink-0"
            >
              <p className="text-overline text-muted-foreground uppercase">On this page</p>
              <ol>
                {contents.map((item) => (
                  <li key={item.id} className="py-1">
                    <a
                      href={`#${item.id}`}
                      className="rounded-sm text-sm text-muted-foreground transition-colors hover:text-foreground"
                    >
                      {item.title}
                    </a>
                  </li>
                ))}
              </ol>
            </nav>
          )}

          <article className="min-w-0 rounded-card border border-border bg-surface p-5 md:p-14 lg:max-w-[800px] lg:flex-1">
            <h1 className="text-h1 text-foreground md:text-display">{title}</h1>
            <p className="mt-2 text-sm text-muted-foreground">
              Last updated <time dateTime={version}>{version}</time>
            </p>
            <div className="mt-6 flex flex-col gap-8 text-lg text-muted-foreground [&_a]:font-semibold [&_a]:text-accent [&_a]:underline [&_a]:underline-offset-2">
              {children}
            </div>
          </article>
        </div>
      </main>
      <LandingFooter {...demoActions} />
    </div>
  );
}
