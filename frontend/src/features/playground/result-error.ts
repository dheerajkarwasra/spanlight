import { errorMessage, isApiError } from "@/lib/api";
import { TEMPLATE_TOO_LARGE_MESSAGE } from "@/lib/prompt-template";

/** How a failed column reads: warning for a stop the user can lift, danger for a failure. */
export interface ResultProblem {
  tone: "danger" | "warning";
  title: string;
  detail: string;
  /** A short stable label shown in mono under the text; null when there is no honest one. */
  label: string | null;
}

const OTHER_COLUMNS = "The other columns are not affected.";

/**
 * `"<status> <code>: <message>"`, the shape `gateway/tracing.py::status_message` writes: the code
 * is Spanlight's own or the provider's error type, so it is shown as is, never renamed.
 */
const PROVIDER_ERROR = /^(\d{3}) ([\w.-]+): ([\s\S]*)$/;

function providerProblem(status: number, code: string, message: string): ResultProblem {
  if (status === 429) {
    return {
      tone: "danger",
      title: "Rate limited",
      detail: `The provider rate limited this request. ${OTHER_COLUMNS}`,
      label: code,
    };
  }
  if (status === 401 || status === 403) {
    return {
      tone: "danger",
      title: "Credential rejected",
      detail: `The provider did not accept this credential. Check it in the gateway settings. ${OTHER_COLUMNS}`,
      label: code,
    };
  }
  if (status === 404) {
    return {
      tone: "danger",
      title: "Model not found",
      detail: `The provider does not know this model for this credential. ${OTHER_COLUMNS}`,
      label: code,
    };
  }
  return {
    tone: "danger",
    title: status >= 500 ? "Provider error" : "Request refused",
    detail: message.trim() || `The provider could not answer. ${OTHER_COLUMNS}`,
    label: code,
  };
}

/** Human copy for one column's `error` string from the server. */
export function problemOf(error: string): ResultProblem {
  if (error === "BUDGET_EXCEEDED") {
    return {
      tone: "warning",
      title: "Budget reached",
      detail:
        "A budget on this project blocks the call. Raise the budget or wait for the next period.",
      label: "BUDGET_EXCEEDED",
    };
  }
  if (error.startsWith("missing_variables:")) {
    const names = error.slice("missing_variables:".length).trim();
    return {
      tone: "danger",
      title: "Missing variables",
      detail: `Fill in ${names || "every variable"} and run again.`,
      label: null,
    };
  }
  if (error === "prompt_too_large") {
    return {
      tone: "danger",
      title: "Prompt too large",
      detail: TEMPLATE_TOO_LARGE_MESSAGE,
      label: "prompt_too_large",
    };
  }
  const provider = PROVIDER_ERROR.exec(error);
  if (provider) {
    return providerProblem(Number(provider[1]), provider[2] ?? "", provider[3] ?? "");
  }
  return {
    tone: "danger",
    title: "The run failed",
    detail: error.trim() || "The provider could not answer.",
    label: null,
  };
}

/** Human copy for a run that failed as a whole, before any column was tried. */
export function requestProblem(error: unknown): ResultProblem {
  if (isApiError(error)) {
    if (error.code === "NOT_CONFIGURED") {
      return {
        tone: "warning",
        title: "Playground is not set up",
        detail:
          "This server has no gateway runtime or credentials key, so it cannot call providers.",
        label: null,
      };
    }
    if (error.status === 404) {
      return {
        tone: "danger",
        title: "Nothing to run",
        detail: "A credential or prompt version in this run no longer exists. Pick again and run.",
        label: null,
      };
    }
    if (error.status === 429) {
      return {
        tone: "warning",
        title: "Too many runs",
        detail: error.detail ?? "You ran the playground too often. Try again shortly.",
        label: null,
      };
    }
  }
  return {
    tone: "danger",
    title: "The run did not start",
    detail: errorMessage(error),
    label: null,
  };
}
