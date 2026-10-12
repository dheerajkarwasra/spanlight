import { getRouteApi } from "@tanstack/react-router";

import { ErrorState } from "@/components/error-state";
import { Notice } from "@/components/notice";
import { PageHeader } from "@/components/page-header";
import { ReadOnlyNote } from "@/components/read-only-note";
import { Skeleton } from "@/components/ui/skeleton";
import { usePermission } from "@/features/shell";
import { isApiError, type Evaluator } from "@/lib/api";

import { BackToEvaluators } from "./back-link";
import { EvaluatorForm } from "./evaluator-form";
import { EVALUATORS_READ_ONLY } from "./evaluator-labels";
import { useEvaluatorQuery, usePromptVersionByIdQuery } from "./evaluator-queries";
import { EMPTY_EVALUATOR_VALUES } from "./evaluator-schema";
import { valuesFromEvaluator } from "./evaluator-values";

const editRoute = getRouteApi("/_authed/$orgId/$projectId/evaluators/$evaluatorId/edit");

function FormSkeleton() {
  return (
    <div role="status" aria-label="Loading the evaluator" className="flex flex-col gap-5">
      <Skeleton className="h-[220px] rounded-card" />
      <Skeleton className="h-[320px] rounded-card" />
    </div>
  );
}

/** Figma "Evaluators — Form": a new evaluator. */
export function NewEvaluatorPage() {
  const canWrite = usePermission("evaluators:write");
  return (
    <div className="flex flex-col gap-6">
      <BackToEvaluators />
      <PageHeader
        title="New evaluator"
        description="Choose how traces get scored. You can change sampling and filters later."
      />
      {canWrite ? (
        <EvaluatorForm evaluator={null} initial={EMPTY_EVALUATOR_VALUES} />
      ) : (
        <ReadOnlyNote>{EVALUATORS_READ_ONLY}</ReadOnlyNote>
      )}
    </div>
  );
}

/** Editing waits for the judge's prompt name, read through the version-by-id route. */
function EditForm({ evaluator }: { evaluator: Evaluator }) {
  const versionId =
    evaluator.config.kind === "llm_judge" ? evaluator.config.prompt_version_id : null;
  const lookup = usePromptVersionByIdQuery(versionId);
  if (versionId !== null && lookup.isPending) {
    return <FormSkeleton />;
  }
  const initial = {
    ...valuesFromEvaluator(evaluator),
    promptName: lookup.data?.prompt_name ?? "",
  };
  const gone = isApiError(lookup.error) && lookup.error.isNotFound;
  return (
    <div className="flex flex-col gap-5">
      {versionId !== null && lookup.isError ? (
        <Notice tone="warning" role="status">
          {gone
            ? "The judge's prompt version is no longer in this project. Pick a template or prompt again before saving."
            : "The judge's prompt version couldn't be loaded. Reload the page to try again before changing the template."}
        </Notice>
      ) : null}
      <EvaluatorForm key={evaluator.id} evaluator={evaluator} initial={initial} />
    </div>
  );
}

/** Figma "Evaluators — Form" for an existing evaluator: the kind is fixed. */
export function EditEvaluatorPage() {
  const { evaluatorId } = editRoute.useParams();
  const canWrite = usePermission("evaluators:write");
  const query = useEvaluatorQuery(evaluatorId);

  return (
    <div className="flex flex-col gap-6">
      <BackToEvaluators />
      <PageHeader
        title={query.data ? `Edit ${query.data.name}` : "Edit evaluator"}
        description="Change what it checks, how much it samples and which traces it sees."
      />
      {!canWrite ? (
        <ReadOnlyNote>{EVALUATORS_READ_ONLY}</ReadOnlyNote>
      ) : query.isPending ? (
        <FormSkeleton />
      ) : query.data === undefined ? (
        <ErrorState
          error={query.error}
          title="Couldn't load this evaluator"
          onRetry={() => {
            void query.refetch();
          }}
        />
      ) : (
        <EditForm evaluator={query.data} />
      )}
    </div>
  );
}
