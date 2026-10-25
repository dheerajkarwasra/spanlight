import { Link } from "@tanstack/react-router";
import { ArrowUpRight, ChevronDown, Circle, CircleCheck } from "lucide-react";
import { useId, useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { RAIL_FOCUS, useCurrentRole, useProjectParams } from "@/features/shell";
import type { Activation } from "@/lib/api";
import { can } from "@/lib/permissions";
import { cn } from "@/lib/utils";

import {
  useActivationQuery,
  useChecklistGate,
  useSetActivationChecklist,
} from "./activation-queries";
import {
  ACTIVATION_STEPS,
  checklistVisible,
  doneCount,
  type ActivationStep,
} from "./activation-steps";

interface ActivationChecklistProps {
  /** `tile` (the full rail): title, count and progress. `icon` (the 56 px rail): a 36 px count. */
  variant: "tile" | "icon";
  /** Beside the rail, or above the tile in the navigation sheet, where there is no room beside it. */
  side?: "right" | "top";
  /** Called after a guide link is followed (closes the navigation sheet). */
  onNavigate?: () => void;
  /** Called when "Hide checklist" removes the tile, to move focus to a control that stays. */
  onHidden?: () => void;
}

/**
 * The "Get set up" checklist in the rail footer (Figma `755:2393`, `758:89031`, `760:981`): a
 * quiet tile that opens a popover of the four setup steps on a click. Renders nothing unless
 * ruling 12 says it shows; the steps are not read at all when it can't.
 */
export function ActivationChecklist({
  variant,
  side = "right",
  onNavigate,
  onHidden,
}: ActivationChecklistProps) {
  const gate = useChecklistGate();
  const activationQuery = useActivationQuery(
    gate.hasTraces === true && !gate.isDemoOrg && !gate.dismissed,
  );
  const setChecklist = useSetActivationChecklist();
  const [open, setOpen] = useState(false);
  const [expanded, setExpanded] = useState<ActivationStep["id"] | null>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const titleId = useId();

  const activation = activationQuery.data;
  if (!activation || !checklistVisible({ ...gate, activation })) {
    // Close with the tile, so a refused hide that brings the tile back doesn't reopen it.
    if (open) {
      setOpen(false);
    }
    return null;
  }
  const done = doneCount(activation);
  const total = ACTIVATION_STEPS.length;
  const followLink = () => {
    setOpen(false);
    onNavigate?.();
  };

  return (
    <Popover
      open={open}
      onOpenChange={(next) => {
        setOpen(next);
        if (next) {
          // A step may have been done since the last read (in another tab, or by a teammate).
          void activationQuery.refetch();
        }
      }}
    >
      <PopoverTrigger ref={trigger} asChild>
        {variant === "tile" ? (
          <button
            type="button"
            className={cn(
              "flex shrink-0 flex-col gap-2.5 rounded-tile bg-rail-tile p-3.5 text-left transition-colors duration-150 hover:bg-rail-tile-hover data-[state=open]:bg-rail-tile-hover",
              RAIL_FOCUS,
            )}
          >
            <span className="flex items-baseline justify-between gap-2">
              <span className="text-sm font-semibold text-rail-foreground">Get set up</span>
              <span className="text-xs font-medium text-rail-subtle-foreground">
                {done} of {total}
              </span>
            </span>
            <span aria-hidden className="flex gap-1">
              {ACTIVATION_STEPS.map((step, index) => (
                <span
                  key={step.id}
                  className={cn(
                    "h-1 flex-1 rounded-full",
                    index < done ? "bg-lime" : "bg-rail-subtle-foreground/40",
                  )}
                />
              ))}
            </span>
          </button>
        ) : (
          <button
            type="button"
            aria-label={`Get set up, ${done} of ${total} done`}
            className={cn(
              "flex size-9 shrink-0 items-center justify-center rounded-lg bg-rail-tile text-xs font-semibold text-lime tabular-nums transition-colors duration-150 hover:bg-rail-tile-hover data-[state=open]:bg-rail-tile-hover",
              RAIL_FOCUS,
            )}
          >
            {done}/{total}
          </button>
        )}
      </PopoverTrigger>
      <PopoverContent
        side={side}
        align={side === "top" ? "start" : "end"}
        // 12 px from the rail's edge: the tile sits 12 px inside it (the rail's padding), the
        // icon 10 px (centred in the 56 px rail).
        sideOffset={side === "top" ? 12 : variant === "tile" ? 24 : 22}
        aria-labelledby={titleId}
        onCloseAutoFocus={(event) => {
          // The tile is gone (hidden, every step done on the read at open, or remounted after a
          // refused hide), so focus can't go back to it.
          if (!trigger.current?.isConnected) {
            event.preventDefault();
            onHidden?.();
          }
        }}
        className={cn(
          "flex flex-col gap-3 p-5",
          side === "top" ? "w-(--radix-popover-trigger-width) p-4" : "w-84",
        )}
      >
        <div className="flex items-baseline justify-between gap-3">
          <h2 id={titleId} className="text-base font-semibold text-foreground">
            Get set up
          </h2>
          <span className="text-xs font-medium text-muted-foreground">
            {done} of {total} done
          </span>
        </div>

        <ul className="flex flex-col gap-1">
          {ACTIVATION_STEPS.map((step) => (
            <StepRow
              key={step.id}
              step={step}
              activation={activation}
              guideId={`${titleId}-${step.id}`}
              expanded={expanded === step.id}
              onToggle={() => {
                setExpanded(expanded === step.id ? null : step.id);
              }}
              onFollowLink={followLink}
            />
          ))}
        </ul>

        <div className="flex flex-wrap items-center justify-between gap-x-3 gap-y-1 border-t border-border pt-3">
          <p className="text-xs text-muted-foreground">Bring it back from your account menu.</p>
          <Button
            variant="link"
            size="sm"
            className="min-h-6"
            onClick={() => {
              setChecklist.mutate(true);
            }}
          >
            Hide checklist
          </Button>
        </div>
      </PopoverContent>
    </Popover>
  );
}

interface StepRowProps {
  step: ActivationStep;
  activation: Activation;
  guideId: string;
  expanded: boolean;
  onToggle: () => void;
  onFollowLink: () => void;
}

/** A done step is a plain row with an icon and "Done"; an undone one discloses its guide. */
function StepRow({ step, activation, guideId, expanded, onToggle, onFollowLink }: StepRowProps) {
  if (activation[step.id]) {
    return (
      <li className="flex min-h-10 items-center gap-3 px-2.5 text-sm text-muted-foreground">
        <CircleCheck aria-hidden className="size-5 shrink-0 text-success" strokeWidth={1.75} />
        <span className="min-w-0 flex-1">{step.label}</span>
        <span className="text-xs font-medium">Done</span>
      </li>
    );
  }
  return (
    <li className={cn("rounded-tile", expanded && "bg-surface-muted")}>
      <button
        type="button"
        aria-expanded={expanded}
        aria-controls={guideId}
        onClick={onToggle}
        className="flex min-h-10 w-full items-center gap-3 rounded-tile px-2.5 text-left text-sm font-semibold text-foreground transition-colors duration-150 hover:bg-surface-hover focus-visible:outline-[1.5px] focus-visible:outline-offset-2 focus-visible:outline-ring"
      >
        <Circle aria-hidden className="size-5 shrink-0 text-border-strong" strokeWidth={1.75} />
        <span className="min-w-0 flex-1">{step.label}</span>
        <ChevronDown
          aria-hidden
          className={cn(
            "size-4 shrink-0 text-muted-foreground transition-transform duration-150",
            expanded && "rotate-180",
          )}
        />
      </button>
      <div
        id={guideId}
        hidden={!expanded}
        className="flex flex-col gap-3 pr-2.5 pb-3 pl-10.5 text-sm text-muted-foreground"
      >
        <p>{step.guide}</p>
        <StepLinks step={step} onFollowLink={onFollowLink} />
      </div>
    </li>
  );
}

/** The guide's links: the first one primary, the rest secondary, wrapping on a narrow popover. */
function StepLinks({ step, onFollowLink }: { step: ActivationStep; onFollowLink: () => void }) {
  const params = useProjectParams();
  const role = useCurrentRole();

  return (
    <div className="flex flex-wrap gap-2">
      {step.links.map((link, index) => {
        const variant = index === 0 ? "primary" : "secondary";
        if ("href" in link) {
          return (
            <Button key={link.label} asChild size="sm" variant={variant}>
              <a href={link.href} target="_blank" rel="noreferrer">
                {link.label}
                <ArrowUpRight aria-hidden />
                <span className="sr-only">(opens in a new tab)</span>
              </a>
            </Button>
          );
        }
        if (link.to === "/onboarding") {
          return (
            <Button key={link.label} asChild size="sm" variant={variant}>
              <Link
                to="/onboarding"
                search={{ org: params.orgId, project: params.projectId }}
                onClick={onFollowLink}
              >
                {link.label}
              </Link>
            </Button>
          );
        }
        if (link.requires && !can(role, link.requires)) {
          return (
            <p key={link.label} className="text-foreground">
              {link.otherwise}
            </p>
          );
        }
        return (
          <Button key={link.label} asChild size="sm" variant={variant}>
            <Link to={link.to} params={params} onClick={onFollowLink}>
              {link.label}
            </Link>
          </Button>
        );
      })}
    </div>
  );
}
