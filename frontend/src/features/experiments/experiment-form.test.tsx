import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ExperimentForm } from "./experiment-form";
import { INITIAL_STATE } from "./experiment-form-state";

interface FakeQuery {
  isPending: boolean;
  isError: boolean;
  data: unknown;
  refetch: () => Promise<unknown>;
}

function loaded(data: unknown): FakeQuery {
  return { isPending: false, isError: false, data, refetch: vi.fn() };
}

function failed(): FakeQuery {
  return { isPending: false, isError: true, data: undefined, refetch: vi.fn() };
}

const queries = vi.hoisted(
  (): Record<"credentials" | "prices" | "evaluators" | "prompts", unknown> => ({
    credentials: null,
    prices: null,
    evaluators: null,
    prompts: null,
  }),
);

vi.mock("./experiment-queries", () => ({
  useCredentialsQuery: () => queries.credentials,
  usePricesQuery: () => queries.prices,
  useEvaluatorsQuery: () => queries.evaluators,
  useEvaluatorBudgets: () => new Map(),
}));
vi.mock("./use-version-index", () => ({
  usePromptListQuery: () => queries.prompts,
  usePromptVersionsQuery: () => loaded([]),
}));

function renderForm(props: { datasetsFailed?: boolean; onRetryDatasets?: () => void } = {}) {
  render(
    <ExperimentForm
      state={INITIAL_STATE}
      errors={{}}
      onChange={() => undefined}
      datasets={props.datasetsFailed ? undefined : []}
      datasetsFailed={props.datasetsFailed ?? false}
      onRetryDatasets={props.onRetryDatasets ?? (() => undefined)}
    />,
  );
}

beforeEach(() => {
  queries.credentials = loaded([]);
  queries.prices = loaded([]);
  queries.evaluators = loaded([]);
  queries.prompts = loaded([]);
});

describe("ExperimentForm when a list fails to load", () => {
  // Finding T7-02: a failed list showed a skeleton forever, an empty select, or a notice
  // without a way to retry.
  it("shows the dataset error with a retry instead of a skeleton", async () => {
    const onRetryDatasets = vi.fn();
    renderForm({ datasetsFailed: true, onRetryDatasets });

    const alert = screen.getByText(/Couldn't load the datasets\./).closest("[role=alert]");
    expect(alert).not.toBeNull();
    await userEvent.click(within(alert as HTMLElement).getByRole("button", { name: "Try again" }));
    expect(onRetryDatasets).toHaveBeenCalledTimes(1);
  });

  it("shows the credential error with a retry instead of an empty select", async () => {
    const credentials = failed();
    queries.credentials = credentials;
    renderForm();

    const alert = screen.getByText(/Couldn't load the credentials\./).closest("[role=alert]");
    expect(alert).not.toBeNull();
    await userEvent.click(within(alert as HTMLElement).getByRole("button", { name: "Try again" }));
    expect(credentials.refetch).toHaveBeenCalledTimes(1);
  });

  it("offers a retry when the prompts fail", async () => {
    const prompts = failed();
    queries.prompts = prompts;
    renderForm();

    const alert = screen.getByText(/Couldn't load the prompts\./).closest("[role=alert]");
    expect(alert).not.toBeNull();
    await userEvent.click(within(alert as HTMLElement).getByRole("button", { name: "Try again" }));
    expect(prompts.refetch).toHaveBeenCalledTimes(1);
  });

  it("offers a retry when the evaluators fail", async () => {
    const evaluators = failed();
    queries.evaluators = evaluators;
    renderForm();

    const alert = screen.getByText(/Couldn't load the evaluators\./).closest("[role=alert]");
    expect(alert).not.toBeNull();
    await userEvent.click(within(alert as HTMLElement).getByRole("button", { name: "Try again" }));
    expect(evaluators.refetch).toHaveBeenCalledTimes(1);
  });
});
