import { Link } from "@tanstack/react-router";
import { Lock } from "lucide-react";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { useProjectParams } from "@/features/shell";
import { errorMessage, type Evaluator } from "@/lib/api";

import { EvaluatorsBreadcrumbs } from "./back-link";
import { KindChip, StatePill } from "./evaluator-chips";
import { evaluatorSummary, samplingLabel, scoreLabel } from "./evaluator-labels";
import { useUpdateEvaluator } from "./evaluator-queries";
import { TestRunDialog } from "./test-run-dialog";

interface EvaluatorHeaderProps {
  evaluator: Evaluator;
  channelName: string | null;
  canWrite: boolean;
}

/** Figma "Evaluators — Detail" header: name, kind, state, summary; Test run, Edit, Disable. */
export function EvaluatorHeader({ evaluator, channelName, canWrite }: EvaluatorHeaderProps) {
  const { orgId, projectId } = useProjectParams();
  const update = useUpdateEvaluator(evaluator.id);

  function toggle() {
    const enabled = !evaluator.enabled;
    update.mutate(
      { enabled },
      {
        onSuccess: () => toast.success(`${enabled ? "Enabled" : "Disabled"} "${evaluator.name}".`),
        onError: (error) => toast.error(errorMessage(error)),
      },
    );
  }

  return (
    <div className="flex flex-col gap-4">
      <EvaluatorsBreadcrumbs current={evaluator.name} />
      <div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between lg:gap-8">
        <div className="flex min-w-0 flex-col gap-2">
          <div className="flex flex-wrap items-center gap-2.5">
            <h1 className="text-h1 [overflow-wrap:anywhere] text-foreground">{evaluator.name}</h1>
            <KindChip kind={evaluator.kind} />
            <StatePill enabled={evaluator.enabled} />
            {canWrite ? null : (
              <Badge variant="neutral">
                <Lock aria-hidden />
                Read only
              </Badge>
            )}
          </div>
          <p className="text-sm text-muted-foreground">
            {evaluatorSummary(evaluator, channelName)} · samples{" "}
            {samplingLabel(evaluator.sampling_rate)} · scores appear on traces as{" "}
            {scoreLabel(evaluator.name)}
          </p>
        </div>
        {canWrite ? (
          <div className="flex flex-wrap items-center gap-2.5 lg:shrink-0 max-sm:[&>*]:flex-1">
            <TestRunDialog evaluator={evaluator} trigger={<Button>Test run</Button>} />
            <Button asChild>
              <Link
                to="/$orgId/$projectId/evaluators/$evaluatorId/edit"
                params={{ orgId, projectId, evaluatorId: evaluator.id }}
              >
                Edit
              </Link>
            </Button>
            <Button variant="ghost" loading={update.isPending} onClick={toggle}>
              {evaluator.enabled ? "Disable" : "Enable"}
            </Button>
          </div>
        ) : null}
      </div>
    </div>
  );
}
