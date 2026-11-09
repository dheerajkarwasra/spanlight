import { Info, Lock } from "lucide-react";
import { useEffect, useId, useRef } from "react";

import { DisabledReason } from "@/components/disabled-reason";
import { RelativeTime } from "@/components/relative-time";
import { RowAction } from "@/components/tile-list";
import { Badge } from "@/components/ui/badge";
import { DialogTrigger } from "@/components/ui/dialog";
import { GithubMark, GoogleMark } from "@/features/auth";
import type { OAuthIdentity, OAuthProvider } from "@/lib/api";
import { formatDate } from "@/lib/format";

import { ConnectProviderDialog } from "./connect-provider-dialog";
import { SignInMethodTile } from "./sign-in-method-tile";
import { PROVIDER_LABELS, type ProviderMethod } from "./sign-in-methods";

const DETAIL = "text-xs font-medium text-muted-foreground";

export function PasswordRow({ isSet, email }: { isSet: boolean; email: string }) {
  return (
    <SignInMethodTile icon={<Lock className="size-[18px]" strokeWidth={1.75} />} title="Password">
      <p className={DETAIL}>
        {isSet
          ? `Sign in with ${email} and your password.`
          : "Not set. Sign out and use Forgot password to set one."}
      </p>
    </SignInMethodTile>
  );
}

function ProviderIcon({ provider }: { provider: OAuthProvider }) {
  return provider === "github" ? (
    <GithubMark className="size-[18px]" />
  ) : (
    <GoogleMark className="size-[18px]" />
  );
}

/** "dheeraj@acme.ai · Connected Sep 2, 2026 · Last used 2 days ago", leaving out what is unknown. */
function ConnectedDetail({ identity }: { identity: OAuthIdentity }) {
  const connected = formatDate(identity.created_at);
  const parts = [identity.email, connected ? `Connected ${connected}` : null].filter(
    (part) => part !== null,
  );

  return (
    <p className={DETAIL}>
      {parts.join(" · ")}
      {parts.length > 0 ? " · " : null}
      Last used <RelativeTime iso={identity.last_used_at} />
    </p>
  );
}

/** What stops a provider from being connected or disconnected, if anything. */
export type ProviderRestriction = "demo" | "unverified";

const UNVERIFIED_REASON = "Verify your email address before connecting a sign-in provider.";
const DEMO_HINT = "Sign-in methods can't be changed on the demo account.";

/** A line under the details with an info icon: why something can't be done (Figma "Hint"). */
function Hint({ id, children }: { id?: string; children: string }) {
  return (
    <p id={id} className={`flex items-center gap-1.5 pt-[3px] ${DETAIL}`}>
      <Info aria-hidden className="size-3.5 shrink-0" strokeWidth={2} />
      {children}
    </p>
  );
}

interface ProviderRowProps {
  method: ProviderMethod;
  /** Why the provider can't be connected or disconnected right now, or null when it can. */
  restriction: ProviderRestriction | null;
  /** Where the sign-in flow returns to afterwards: this page. */
  returnTo: string;
  disconnecting: boolean;
  onDisconnect: () => void;
}

export function ProviderRow({
  method,
  restriction,
  returnTo,
  disconnecting,
  onDisconnect,
}: ProviderRowProps) {
  const { provider, identity, onlyMethod } = method;
  const label = PROVIDER_LABELS[provider];
  const hintId = useId();
  const icon = <ProviderIcon provider={provider} />;
  const connectRef = useRef<HTMLButtonElement>(null);
  const disconnectRequested = useRef(false);

  // Disconnect is replaced by Connect once the provider is gone, taking keyboard focus with it:
  // move focus to the new Connect button rather than let it fall to the page (WCAG 2.4.3).
  useEffect(() => {
    if (disconnecting || !disconnectRequested.current) {
      return;
    }
    disconnectRequested.current = false;
    connectRef.current?.focus();
  }, [disconnecting, identity]);

  // The shared demo account can't link or unlink anything, so there is no button to press: the
  // server refuses changes to its sign-in methods.
  if (restriction === "demo") {
    return (
      <SignInMethodTile
        icon={icon}
        title={label}
        badge={identity ? <Badge variant="success">Connected</Badge> : null}
      >
        {identity ? (
          <ConnectedDetail identity={identity} />
        ) : (
          <p className={DETAIL}>Not connected</p>
        )}
        <Hint>{DEMO_HINT}</Hint>
      </SignInMethodTile>
    );
  }

  if (identity === null) {
    return (
      <SignInMethodTile
        icon={icon}
        title={label}
        action={
          restriction === "unverified" ? (
            <DisabledReason reason={UNVERIFIED_REASON}>
              <RowAction disabled aria-label={`Connect ${label}`}>
                Connect
              </RowAction>
            </DisabledReason>
          ) : (
            <ConnectProviderDialog provider={provider} next={returnTo}>
              <DialogTrigger asChild>
                <RowAction ref={connectRef} aria-label={`Connect ${label}`}>
                  Connect
                </RowAction>
              </DialogTrigger>
            </ConnectProviderDialog>
          )
        }
      >
        <p className={DETAIL}>Not connected</p>
      </SignInMethodTile>
    );
  }

  return (
    <SignInMethodTile
      icon={icon}
      title={label}
      badge={<Badge variant="success">Connected</Badge>}
      action={
        <RowAction
          disabled={onlyMethod}
          loading={disconnecting}
          aria-label={`Disconnect ${label}`}
          aria-describedby={onlyMethod ? hintId : undefined}
          onClick={() => {
            disconnectRequested.current = true;
            onDisconnect();
          }}
        >
          Disconnect
        </RowAction>
      }
    >
      <ConnectedDetail identity={identity} />
      {onlyMethod ? (
        <Hint id={hintId}>It's your only way to sign in, so it can't be disconnected.</Hint>
      ) : null}
    </SignInMethodTile>
  );
}
