import { useState } from "react";

import { Breadcrumbs, Crumb } from "@/components/breadcrumbs";
import { DisabledReason } from "@/components/disabled-reason";
import { Button } from "@/components/ui/button";
import { useProjectParams } from "@/features/shell";
import type { AlertEvent, AlertRule } from "@/lib/api";

import { AcknowledgeButton } from "./acknowledge-button";
import { MuteButton } from "./mute-button";
import { RuleActionsMenu } from "./rule-actions-menu";
import { RuleEditorDialog } from "./rule-editor-dialog";
import { isFiring } from "./rule-state";
import { ruleSummary } from "./rule-summary";

/** "Alerts › <current>": the trail back to the rules list. */
export function AlertsBreadcrumbs({ current }: { current: string }) {
  const { orgId, projectId } = useProjectParams();
  return (
    <Breadcrumbs current={current}>
      <Crumb to="/$orgId/$projectId/alerts" params={{ orgId, projectId }}>
        Alerts
      </Crumb>
    </Breadcrumbs>
  );
}

interface RuleDetailHeaderProps {
  rule: AlertRule;
  /** The open event; undefined while the events load or when they failed. */
  openEvent: AlertEvent | null | undefined;
  /** The events couldn't be loaded, so the open event to acknowledge is unknown. */
  eventsFailed: boolean;
  canWrite: boolean;
  now: Date;
}

/**
 * Figma "Rule header": breadcrumbs, name, summary line and the actions: Edit, "…", Mute or
 * Unmute, and Acknowledge while an event is open. Without `alerts:write` they are hidden; the
 * page's banner says why.
 */
export function RuleDetailHeader({
  rule,
  openEvent,
  eventsFailed,
  canWrite,
  now,
}: RuleDetailHeaderProps) {
  const { projectId } = useProjectParams();
  const [editing, setEditing] = useState(false);

  return (
    <div className="flex flex-col gap-6">
      <AlertsBreadcrumbs current={rule.name} />
      <div className="flex flex-col gap-4 lg:flex-row lg:items-end lg:justify-between lg:gap-8">
        <div className="flex min-w-0 flex-col gap-2">
          <h1 className="text-h1 [overflow-wrap:anywhere] text-foreground">{rule.name}</h1>
          <p className="text-sm text-muted-foreground">{ruleSummary(rule)}</p>
        </div>
        <div className="flex flex-wrap items-center gap-2.5 lg:shrink-0 lg:flex-nowrap">
          {canWrite ? (
            <>
              <Button
                className="flex-1 sm:flex-none"
                onClick={() => {
                  setEditing(true);
                }}
              >
                Edit
              </Button>
              <RuleActionsMenu rule={rule} />
              <MuteButton rule={rule} now={now} className="flex-1 sm:flex-none" />
            </>
          ) : null}
          {openEvent ? (
            <AcknowledgeButton
              event={openEvent}
              canWrite={canWrite}
              now={now}
              className="max-sm:order-first max-sm:w-full"
            />
          ) : null}
          {canWrite && openEvent === undefined && eventsFailed && isFiring(rule) ? (
            <span className="max-sm:order-first max-sm:w-full">
              <DisabledReason reason="The rule's events couldn't be loaded, so the open event is unknown. Retry in the Events card.">
                <Button variant="primary" disabled className="max-sm:w-full">
                  Acknowledge
                </Button>
              </DisabledReason>
            </span>
          ) : null}
        </div>
      </div>
      <RuleEditorDialog
        open={editing}
        onOpenChange={setEditing}
        projectId={projectId}
        rule={rule}
      />
    </div>
  );
}
