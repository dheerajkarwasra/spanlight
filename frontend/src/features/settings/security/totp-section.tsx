import { useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { toast } from "sonner";

import { ErrorState } from "@/components/error-state";
import { Card } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { useMe } from "@/features/auth";
import { queryKeys, securityApi, type TotpSetup, type TotpStatus } from "@/lib/api";
import { useUncachedAction } from "@/lib/use-uncached-action";

import { useTotpStatusQuery } from "./security-queries";
import { TotpDisableDialog } from "./totp-disable-dialog";
import { classifyTotpError } from "./totp-flow";
import { TotpPasswordDialog } from "./totp-password-dialog";
import { TotpSetupDialog } from "./totp-setup-dialog";
import { TwoFactorCard, type TwoFactorCardState } from "./two-factor-card";

/**
 * Settings › Security › Two-factor authentication: the card, the setup wizard and the turn-off
 * dialog. The setup secret is kept here, in state, from the answer to "Turn on" until the wizard
 * closes. It never goes into the query cache, storage or a log.
 */
export function TotpSection() {
  const { user, has_password: hasPassword, email_verification_required: unverified } = useMe();
  const queryClient = useQueryClient();
  const status = useTotpStatusQuery();
  const start = useUncachedAction(securityApi.totpSetup);
  const [setup, setSetup] = useState<TotpSetup | null>(null);
  const [notAvailable, setNotAvailable] = useState(false);
  const [turnOffOpen, setTurnOffOpen] = useState(false);
  const [passwordOpen, setPasswordOpen] = useState(false);

  function alreadyEnabled() {
    void queryClient.invalidateQueries({ queryKey: queryKeys.totp });
    void queryClient.invalidateQueries({ queryKey: queryKeys.me });
  }

  async function turnOn() {
    setNotAvailable(false);
    if (hasPassword) {
      setPasswordOpen(true);
      return;
    }
    const result = await start.run();
    if (result.ok) {
      setSetup(result.value);
      return;
    }
    const failure = classifyTotpError(result.error);
    if (failure.kind === "not-configured") {
      setNotAvailable(true);
    } else if (failure.kind === "already-enabled") {
      alreadyEnabled();
    } else {
      toast.error(failure.kind === "other" ? failure.message : "That didn't work. Try again.");
    }
  }

  return (
    <>
      {status.data ? (
        <TwoFactorCard
          state={cardState(status.data, unverified ? user.email : null, {
            starting: start.pending,
            notAvailable,
          })}
          onTurnOn={() => {
            void turnOn();
          }}
          onTurnOff={() => {
            setTurnOffOpen(true);
          }}
        />
      ) : status.isError ? (
        <Card>
          <ErrorState
            compact
            error={status.error}
            title="Couldn't load two-factor authentication"
            onRetry={() => {
              void status.refetch();
            }}
          />
        </Card>
      ) : (
        <div role="status" aria-label="Loading two-factor authentication">
          <Skeleton className="h-[97px] rounded-card" />
        </div>
      )}
      {/* Outside the branches above: a failed refresh must not close a wizard that holds the codes. */}
      {setup ? (
        <TotpSetupDialog
          setup={setup}
          onClose={() => {
            setSetup(null);
          }}
          onNotAvailable={() => {
            setNotAvailable(true);
          }}
        />
      ) : null}
      <TotpPasswordDialog
        open={passwordOpen}
        onOpenChange={setPasswordOpen}
        onSetup={setSetup}
        onAlreadyEnabled={alreadyEnabled}
      />
      <TotpDisableDialog open={turnOffOpen} onOpenChange={setTurnOffOpen} />
    </>
  );
}

function cardState(
  status: TotpStatus,
  /** The address to verify, or null when nothing needs verifying. */
  unverifiedEmail: string | null,
  off: { starting: boolean; notAvailable: boolean },
): TwoFactorCardState {
  if (status.enabled) {
    return {
      kind: "on",
      since: status.enabled_at,
      recoveryCodesRemaining: status.recovery_codes_remaining,
    };
  }
  if (unverifiedEmail !== null) {
    return { kind: "email-unverified", email: unverifiedEmail };
  }
  return { kind: "off", ...off };
}
