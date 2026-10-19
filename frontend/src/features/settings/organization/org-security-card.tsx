import { useId, useState } from "react";

import { DisabledReason } from "@/components/disabled-reason";
import { Badge } from "@/components/ui/badge";
import { Card } from "@/components/ui/card";
import { Switch } from "@/components/ui/switch";
import { useMe } from "@/features/auth";
import type { Org } from "@/lib/api";

import { EnableOwnTwoFactorCallout, NotConfiguredCallout } from "./org-security-callouts";
import {
  mustEnableOwnTwoFactor,
  requirementDescription,
  securitySubtitle,
} from "./organization-flow";
import { RequireTwoFactorDialog } from "./require-two-factor-dialog";
import { RequireTwoFactorTile } from "./require-two-factor-tile";
import { useRequireTwoFactor } from "./use-require-two-factor";

interface OrgSecurityCardProps {
  org: Org;
  /** Owners (`org:security`): a switch that saves on its own. Everyone else sees the status. */
  canChange: boolean;
  /** False under the page's view-only banner, which already says who can change things. */
  sayWhoCanChange?: boolean;
}

/**
 * The "Security" card (Figma "Settings/Org security card"). Turning the switch on asks first, then
 * saves; turning it off saves at once. The switch shows the stored value, so a refused save (`409
 * NOT_CONFIGURED`, `409 TWO_FACTOR_NOT_ENABLED`) leaves it off and the card says why.
 */
export function OrgSecurityCard({ org, canChange, sayWhoCanChange = true }: OrgSecurityCardProps) {
  const titleId = useId();
  const switchId = useId();
  const descriptionId = useId();
  const me = useMe();
  const { save, pending, problem } = useRequireTwoFactor(org);
  const [confirming, setConfirming] = useState(false);
  const mustEnableOwn =
    canChange &&
    mustEnableOwnTwoFactor({ required: org.require_2fa, ownerHasTwoFactor: me.totp_enabled });

  async function confirmRequire() {
    // A refusal is explained by the card, so the dialog closes; a failure keeps it open to retry.
    if ((await save(true)) !== "failed") {
      setConfirming(false);
    }
  }

  const toggle = (
    <Switch
      id={switchId}
      aria-describedby={descriptionId}
      checked={org.require_2fa}
      disabled={pending || mustEnableOwn}
      onCheckedChange={(next) => {
        if (next) {
          setConfirming(true);
        } else {
          void save(false);
        }
      }}
    />
  );

  return (
    <Card role="region" aria-labelledby={titleId} className="flex flex-col gap-[18px] p-5 sm:p-6">
      <div className="flex flex-col gap-1">
        <div className="flex items-center gap-2.5">
          <h2 id={titleId} className="text-card">
            Security
          </h2>
          {canChange ? <Badge>Owners only</Badge> : null}
        </div>
        <p className="text-xs font-medium text-muted-foreground">{securitySubtitle(org.name)}</p>
      </div>

      <RequireTwoFactorTile
        {...(canChange ? { labelFor: switchId } : {})}
        descriptionId={descriptionId}
        description={requirementDescription(org.name, org.require_2fa, canChange, sayWhoCanChange)}
      >
        {canChange ? (
          mustEnableOwn ? (
            <DisabledReason reason="Turn on two-factor authentication for your own account first.">
              {toggle}
            </DisabledReason>
          ) : (
            toggle
          )
        ) : (
          <Badge variant={org.require_2fa ? "success" : "neutral"}>
            {org.require_2fa ? "Required" : "Not required"}
          </Badge>
        )}
      </RequireTwoFactorTile>

      {problem === "not-configured" ? <NotConfiguredCallout /> : null}
      {mustEnableOwn ? <EnableOwnTwoFactorCallout answered={problem === "not-enabled"} /> : null}

      <RequireTwoFactorDialog
        open={confirming}
        onOpenChange={setConfirming}
        orgName={org.name}
        pending={pending}
        onConfirm={() => {
          void confirmRequire();
        }}
      />
    </Card>
  );
}
