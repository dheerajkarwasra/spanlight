import type { BillingSource } from "@/lib/api";

import { KIND_CHOICES, PROVIDER_LABELS, formatUtc } from "./source-model";

/**
 * The reconciliation page's sentences about its source (Figma "Reconciliation / overview",
 * "/ never fetched", "/ failing source"): the line under the title, the table's footnote and the
 * two callouts. Pure, so each wording is tested against the source's state.
 */

const REPORT_NAMES: Record<BillingSource["kind"], string> = {
  anthropic_admin_api: "the Anthropic cost report",
  openai_costs_api: "the OpenAI Costs API",
  csv_upload: "the CSV files uploaded to this source",
};

/** Status lines a new admin key fixes (`fetch_policy._KEY_LINES` and HTTP 401/403). */
function isKeyProblem(line: string | null): boolean {
  return line !== null && (/^40[13] /.test(line) || line === "key cannot be decrypted");
}

/** "Support Copilot", "Support Copilot and Eval harness", "A, B and C". */
function projectList(source: BillingSource): string {
  const names = source.projects.map((project) => project.name);
  if (names.length === 0) {
    return "no projects yet";
  }
  const last = names.pop() ?? "";
  return names.length === 0 ? last : `${names.join(", ")} and ${last}`;
}

/** "Anthropic Admin API · compares Support Copilot and Eval harness · last success Oct 15, 03:31 UTC". */
export function sourceSummary(source: BillingSource): string {
  const kind = KIND_CHOICES.find((choice) => choice.value === source.kind)?.title ?? source.kind;
  const verb = source.kind === "csv_upload" ? "last upload" : "last success";
  const when =
    source.last_success_at === null
      ? `added ${formatUtc(source.created_at)}`
      : `${verb} ${formatUtc(source.last_success_at)}`;
  return `${kind} · compares ${projectList(source)} · ${when}`;
}

/** The note under the daily table: where the provider's figures come from and when a day settles. */
export function tableFootnote(source: BillingSource): string {
  const from = REPORT_NAMES[source.kind];
  if (source.status === "never_fetched") {
    const schedule =
      source.kind === "csv_upload" ? "" : ", fetched daily at 03:30 UTC for the last 7 days";
    return `Provider figures come from ${from}${schedule}. — marks a figure Spanlight doesn’t have yet; hover or focus it for the reason.`;
  }
  const schedule = source.kind === "csv_upload" ? "" : ", fetched daily at 03:30 UTC";
  return `Provider figures come from ${from}${schedule}. A day is provisional until 48 h after it ends and a fetch has run past that point; only settled days count toward “within 2 %”.`;
}

export interface CalloutCopy {
  title: string;
  body: string;
  /** Offer "Rotate key": an API source whose admin key was refused or can't be decrypted. */
  rotate: boolean;
}

/** Figma "Callout · Never fetched": nothing from the provider yet. */
export function neverFetchedCopy(source: BillingSource): CalloutCopy {
  const body =
    source.kind === "csv_upload"
      ? "Nothing has been uploaded to this source. Upload the provider’s cost CSV to fill the provider side; until then the table shows Spanlight’s side only."
      : "This source hasn’t been fetched. The daily fetch runs at 03:30 UTC, and Refresh fetches it now. Until then the table shows Spanlight’s side only.";
  return { title: "No provider data yet", body, rotate: false };
}

/** Whether a day-range label names more than one day ("Oct 12 – Oct 14", "Oct 3, Oct 9"). */
function several(label: string): boolean {
  return label.includes("–") || label.includes(",");
}

/** Figma "Callout · Fetch failing": what failed, what to do, and which days it leaves open. */
export function failingCopy(
  source: BillingSource,
  ranges: { unfetched: string | null; provisional: string | null },
): CalloutCopy {
  const line = source.last_error;
  const rotate = source.kind !== "csv_upload" && isKeyProblem(line);
  const provider = PROVIDER_LABELS[source.provider];
  const since =
    source.last_success_at === null
      ? ""
      : ` The last success was ${formatUtc(source.last_success_at)}.`;
  let action =
    source.kind === "csv_upload"
      ? "Upload the file again once it is fixed."
      : "Refresh tries again now, and the daily fetch retries at 03:30 UTC.";
  if (rotate) {
    action = `${provider} refused the admin key. Rotate the key to resume the daily fetch.`;
  } else if (line === "CREDENTIALS_KEYS is not set") {
    action = "Ask your administrator to set CREDENTIALS_KEYS on the server.";
  }
  const open = [
    ranges.unfetched
      ? `${ranges.unfetched} ${several(ranges.unfetched) ? "have" : "has"} no provider figures`
      : null,
    ranges.provisional
      ? `${ranges.provisional} ${several(ranges.provisional) ? "stay" : "stays"} provisional`
      : null,
  ].filter((part) => part !== null);
  const until = open.length > 0 ? ` Until a fetch succeeds, ${open.join(" and ")}.` : "";
  return {
    title: line ? `The last fetch failed: ${line}` : "The last fetch failed",
    body: `${action}${until}${since}`,
    rotate,
  };
}
