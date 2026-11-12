import type { ComponentType, ReactNode, SVGProps } from "react";

import { oauthStartUrl, type OAuthProvider } from "@/lib/api";

import { Button } from "./auth-controls";
import { rememberOAuthProvider } from "./oauth-provider-hint";
import { useOAuthProviders } from "./oauth-queries";
import { GithubMark, GoogleMark } from "./provider-icons";
import { safeNextPath } from "./search";

const PROVIDERS: Record<
  OAuthProvider,
  { label: string; Icon: ComponentType<SVGProps<SVGSVGElement>> }
> = {
  github: { label: "GitHub", Icon: GithubMark },
  google: { label: "Google", Icon: GoogleMark },
};

interface OAuthButtonsProps {
  /** The page to land on after signing in, as it came in the URL. */
  next: string | undefined;
  /** What the divider under the buttons says, e.g. "or sign in with email". */
  dividerLabel: string;
  /** A line under the buttons, above the divider (signup's terms line). */
  note?: ReactNode;
}

/**
 * "Continue with GitHub / Google", for the providers this server has set up, then the divider that
 * leads into the email form. Until the list arrives, when it is empty, and when it fails to load,
 * it renders nothing at all: the password form is the primary way in and never waits on this.
 */
export function OAuthButtons({ next, dividerLabel, note }: OAuthButtonsProps) {
  const providers = useOAuthProviders();
  // Ignore a provider this build has no button for rather than break the whole sign-in screen.
  const available = (providers.data ?? []).filter(({ provider }) => provider in PROVIDERS);
  if (available.length === 0) {
    return null;
  }

  return (
    <>
      <div className="flex flex-col gap-2.5">
        <div className="flex flex-col gap-2.5">
          {available.map(({ provider }) => {
            const { label, Icon } = PROVIDERS[provider];
            return (
              <Button key={provider} asChild size="lg" className="w-full">
                {/* A full-page navigation: the server answers with a redirect to the provider. */}
                <a
                  href={oauthStartUrl(provider, { next: safeNextPath(next) ?? undefined })}
                  onClick={() => {
                    rememberOAuthProvider(provider);
                  }}
                >
                  <Icon className="size-[18px]" />
                  Continue with {label}
                </a>
              </Button>
            );
          })}
        </div>
        {note}
      </div>
      <div
        className="flex items-center gap-3.5 font-mono text-xs leading-[1.4] text-mono-600"
        aria-hidden
      >
        <span className="h-px flex-1 bg-mono-line-strong" />
        {dividerLabel}
        <span className="h-px flex-1 bg-mono-line-strong" />
      </div>
    </>
  );
}
