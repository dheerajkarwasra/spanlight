import { CircleAlert } from "lucide-react";
import { useId } from "react";

import { SectionCard } from "@/components/section-card";
import { Switch } from "@/components/ui/switch";
import { useMe } from "@/features/auth";
import { errorMessage } from "@/lib/api";

import { useSetWeeklyDigest } from "./account-queries";

/**
 * Settings › Account › Email (Figma "Email card"): the caller's own weekly digest switch, for every
 * organization. It saves on toggle; while saving the switch is disabled, and if the save fails it
 * flips back and the error shows under the helper text.
 */
export function EmailSection() {
  const { weekly_digest } = useMe();
  const setDigest = useSetWeeklyDigest();
  const switchId = useId();
  const labelId = useId();
  const helperId = useId();
  const errorId = useId();
  // Shows the value being saved; on failure the cached value, unchanged, takes over again.
  const checked = setDigest.isPending ? setDigest.variables : weekly_digest;

  return (
    <SectionCard title="Email" description="Emails Spanlight sends to you.">
      <div className="flex flex-col gap-2">
        {/* The whole tile toggles the switch; the switch is named by the title alone. */}
        <label
          htmlFor={switchId}
          className="flex cursor-pointer items-center gap-4 rounded-tile bg-surface-muted p-4"
        >
          <span className="flex min-w-0 flex-1 flex-col gap-1 text-sm">
            <span id={labelId} className="font-semibold text-foreground">
              Weekly digest
            </span>
            <span id={helperId} className="text-muted-foreground">
              A Monday summary of each project&apos;s week. Applies to every organization you belong
              to.
            </span>
          </span>
          <Switch
            id={switchId}
            aria-labelledby={labelId}
            aria-describedby={setDigest.isError ? `${helperId} ${errorId}` : helperId}
            checked={checked}
            disabled={setDigest.isPending}
            onCheckedChange={(value) => {
              setDigest.mutate(value);
            }}
          />
        </label>
        {setDigest.isError ? (
          <p
            id={errorId}
            role="alert"
            className="flex items-start gap-1.5 text-xs font-medium text-danger-text"
          >
            <CircleAlert aria-hidden className="mt-px size-3.5 shrink-0" strokeWidth={2.25} />
            {errorMessage(setDigest.error)}
          </p>
        ) : null}
      </div>
    </SectionCard>
  );
}
