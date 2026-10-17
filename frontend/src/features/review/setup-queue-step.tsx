import { Ban, Plus } from "lucide-react";
import { useId, type ReactNode } from "react";

import { Badge } from "@/components/ui/badge";
import type { AnnotationQueue, ScoreConfig } from "@/lib/api";
import { pluralize } from "@/lib/format";
import { cn } from "@/lib/utils";

import { DAILY_REVIEW_QUEUE, eligibleQueues } from "./setup-steps";

interface SetupQueueStepProps {
  queues: readonly AnnotationQueue[];
  configs: readonly ScoreConfig[];
  /** The failure_category config; null when step 1 creates it. */
  configId: string | null;
  /** The picked queue; null to create "Daily review". */
  value: string | null;
  onChange: (queueId: string | null) => void;
}

/**
 * Step 2, "Queue": create "Daily review", or pick a queue that already asks for failure_category.
 * The others are listed disabled with the reason.
 */
export function SetupQueueStep({
  queues,
  configs,
  configId,
  value,
  onChange,
}: SetupQueueStepProps) {
  const titleId = useId();
  const eligible = new Set(eligibleQueues(queues, configId).map((queue) => queue.id));
  const names = new Map(configs.map((config) => [config.id, config.name]));

  return (
    <fieldset className="flex flex-col gap-3" aria-labelledby={titleId}>
      <div className="flex flex-col gap-1">
        <h3 id={titleId} className="text-base font-bold text-foreground">
          Queue
        </h3>
        <p className="text-sm text-muted-foreground">
          Selected traces go into one queue. It must ask for failure_category.
        </p>
      </div>
      <QueueOption
        checked={value === null}
        onSelect={() => onChange(null)}
        title={
          <>
            Create “{DAILY_REVIEW_QUEUE}”
            <Badge variant="accent" size="sm">
              <Plus aria-hidden />
              New
            </Badge>
          </>
        }
        detail="A new queue that asks for failure_category. Add more scores to it later."
      />
      {queues.map((queue) => {
        const ok = eligible.has(queue.id);
        const scores = queue.score_config_ids.flatMap((id) => names.get(id) ?? []).join(", ");
        return (
          <QueueOption
            key={queue.id}
            checked={value === queue.id}
            disabled={!ok}
            onSelect={() => onChange(queue.id)}
            title={
              <>
                <span className="font-mono [overflow-wrap:anywhere]">{queue.name}</span>
                {ok ? null : (
                  <Badge variant="outline" size="sm">
                    <Ban aria-hidden />
                    Not eligible
                  </Badge>
                )}
              </>
            }
            detail={
              ok
                ? `Asks for ${scores} · ${pluralize(queue.progress.pending, "pending item")}`
                : "Doesn't ask for failure_category"
            }
          />
        );
      })}
    </fieldset>
  );
}

interface QueueOptionProps {
  checked: boolean;
  disabled?: boolean;
  onSelect: () => void;
  title: ReactNode;
  detail: string;
}

function QueueOption({ checked, disabled = false, onSelect, title, detail }: QueueOptionProps) {
  return (
    <label
      className={cn(
        "flex items-start gap-3 rounded-tile border px-4 py-3",
        disabled ? "cursor-not-allowed border-transparent bg-surface-muted" : "cursor-pointer",
        checked ? "border-accent bg-surface-selected" : !disabled && "border-border bg-surface",
      )}
    >
      <input
        type="radio"
        name="review-queue"
        checked={checked}
        disabled={disabled}
        onChange={onSelect}
        className="mt-1 accent-[var(--accent)]"
      />
      <span className="flex min-w-0 flex-col gap-1">
        <span className="flex flex-wrap items-center gap-2 text-sm font-semibold text-foreground">
          {title}
        </span>
        <span className="text-xs text-muted-foreground">{detail}</span>
      </span>
    </label>
  );
}
