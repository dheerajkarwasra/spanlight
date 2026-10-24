import { getRouteApi, Link, useNavigate } from "@tanstack/react-router";
import { useState, type ReactNode } from "react";

import { Notice } from "@/components/notice";
import { PageHeader } from "@/components/page-header";
import { UnknownValue } from "@/components/unknown-value";
import { Button } from "@/components/ui/button";
import { Card, CardTitle } from "@/components/ui/card";
import { usePermission, useProjectParams } from "@/features/shell";
import { formatCost, formatInteger } from "@/lib/format";

import { ExperimentsBreadcrumbs } from "./back-link";
import { ExperimentForm } from "./experiment-form";
import {
  DEFAULT_MAX_COST,
  INITIAL_STATE,
  MAX_ITEMS_CEILING,
  parseNumber,
  prefillState,
  serverErrors,
  toCreate,
  validate,
  type FormErrors,
  type FormState,
} from "./experiment-form-state";
import { useCreateExperiment, useDatasetsQuery } from "./experiment-queries";
import { usePromptVersionsQuery } from "./use-version-index";

const route = getRouteApi("/_authed/$orgId/$projectId/experiments/new");

const READ_ONLY =
  "Your role can view experiments but not create them. Members and above create and run experiments, because every run makes real provider calls.";

/** Figma "Experiments — Create": the form, with what a run will do summed up beside it. */
export function NewExperimentPage() {
  const { orgId, projectId } = useProjectParams();
  const navigate = useNavigate();
  const canRun = usePermission("experiments:run");
  const datasets = useDatasetsQuery();
  const create = useCreateExperiment();
  const [state, setState] = useState<FormState>(INITIAL_STATE);
  const [errors, setErrors] = useState<FormErrors>({});
  const [checked, setChecked] = useState(false);
  const search = route.useSearch();
  const prefillVersions = usePromptVersionsQuery(search.prompt);
  const [prefilled, setPrefilled] = useState(false);

  // Once, when the lists the ids are checked against have loaded (a failed datasets load waits for
  // its retry). The form is usable meanwhile, so only fields still at their initial value are
  // filled: what the user already set stays.
  if (!prefilled && datasets.isSuccess && !(search.prompt && prefillVersions.isPending)) {
    const patch = prefillState(search, prefillVersions.data);
    if (!datasets.data.some((item) => item.id === patch.datasetId)) delete patch.datasetId;
    // The prompt's versions failed to load (usually: no prompt by that name).
    if (prefillVersions.isError) delete patch.promptName;
    setPrefilled(true);
    setState((current) => {
      const fresh = Object.fromEntries(
        Object.entries(patch).filter(
          ([key]) => current[key as keyof FormState] === INITIAL_STATE[key as keyof FormState],
        ),
      ) as Partial<FormState>;
      // A version, and the source and model it brings, belong to its prompt: none once the user
      // picked a prompt of their own.
      if (fresh.promptName === undefined) {
        delete fresh.versionId;
        delete fresh.source;
        delete fresh.model;
      }
      return { ...current, ...fresh };
    });
  }

  function change(patch: Partial<FormState>) {
    const next = { ...state, ...patch };
    setState(next);
    if (checked) {
      setErrors(validate(next));
    }
  }

  function submit() {
    const found = validate(state);
    setChecked(true);
    setErrors(found);
    if (Object.keys(found).length > 0) {
      return;
    }
    create.mutate(toCreate(state), {
      onSuccess: (experiment) =>
        void navigate({
          to: "/$orgId/$projectId/experiments/$experimentId",
          params: { orgId, projectId, experimentId: experiment.id },
        }),
      onError: (error) => setErrors(serverErrors(error)),
    });
  }

  const dataset = datasets.data?.find((item) => item.id === state.datasetId);
  const hasProblems = Object.keys(errors).length > 0;

  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-col gap-2">
        <ExperimentsBreadcrumbs current="New experiment" />
        <PageHeader
          title="New experiment"
          description="Choose a dataset, what to run on it and how to score the results."
        />
      </div>
      {!canRun ? (
        <Notice>{READ_ONLY}</Notice>
      ) : (
        <form
          noValidate
          className="grid items-start gap-5 lg:grid-cols-[minmax(0,1fr)_360px]"
          onSubmit={(event) => {
            event.preventDefault();
            submit();
          }}
        >
          <div className="flex flex-col gap-5">
            <ExperimentForm
              state={state}
              errors={errors}
              onChange={change}
              datasets={datasets.data}
              datasetsFailed={datasets.isError}
              onRetryDatasets={() => void datasets.refetch()}
            />
            {errors.form ? (
              <Notice tone="danger" role="alert">
                {errors.form}
              </Notice>
            ) : hasProblems ? (
              <Notice tone="danger" role="alert">
                Some fields need a look before the experiment can be created.
              </Notice>
            ) : null}
            <div className="flex flex-wrap justify-end gap-2">
              <Button asChild>
                <Link to="/$orgId/$projectId/experiments" params={{ orgId, projectId }}>
                  Cancel
                </Link>
              </Button>
              <Button type="submit" variant="primary" loading={create.isPending}>
                Create experiment
              </Button>
            </div>
          </div>
          <BeforeYouRun
            items={
              parseNumber(state.maxItems, { min: 1, max: MAX_ITEMS_CEILING, integer: true }) ??
              (dataset ? Math.min(dataset.item_count, MAX_ITEMS_CEILING) : null)
            }
            maxCost={state.maxCost.trim() || DEFAULT_MAX_COST}
          />
        </form>
      )}
    </div>
  );
}

function BeforeYouRun({ items, maxCost }: { items: number | null; maxCost: string }) {
  const rows: [string, ReactNode][] = [
    [
      "Items",
      items === null ? <UnknownValue reason="Choose a dataset first." /> : formatInteger(items),
    ],
    ["Cost limit", formatCost(maxCost) ?? <UnknownValue reason="Not a valid amount yet." />],
    ["Concurrency", "4 at a time"],
  ];
  return (
    <Card className="flex flex-col gap-4 p-6 lg:sticky lg:top-6">
      <CardTitle>Before you run</CardTitle>
      <div className="flex flex-col gap-2 text-sm text-muted-foreground">
        <p>
          Each run is a real provider call. Calls show up as traces in the experiments environment.
        </p>
        <p>Scores are also written on each trace, as Eval · name.</p>
      </div>
      <dl className="flex flex-col gap-3">
        {rows.map(([label, value]) => (
          <div key={label} className="flex items-center justify-between gap-3">
            <dt className="text-xs font-medium text-muted-foreground">{label}</dt>
            <dd className="text-sm font-semibold text-foreground tabular">{value}</dd>
          </div>
        ))}
      </dl>
    </Card>
  );
}
