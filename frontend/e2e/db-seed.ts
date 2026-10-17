import { execFileSync } from "node:child_process";
import { randomBytes, randomUUID } from "node:crypto";

/*
 * Rows the public API cannot create, written straight into the stack's database with `psql`:
 * a provider status page incident (the worker polls the real feeds, so a test cannot choose one)
 * and a Doctor insight (detectors need minutes of traffic to open one). Values travel as psql
 * variables (`:'name'`), so they are quoted by psql and never spliced into the SQL text.
 *
 * `E2E_DATABASE_URL` must name a role that may write these tables past row-level security, such
 * as the dev container's `postgres` user. Tests that need it skip without it.
 */

const DATABASE_URL = process.env.E2E_DATABASE_URL;

export const dbEnabled = Boolean(DATABASE_URL);

export const DB_SKIP_REASON =
  "Set E2E_DATABASE_URL (a role that may write past row-level security, e.g. the dev container's postgres user) to seed incidents and insights.";

function psql(script: string, variables: Record<string, string>): string {
  if (!DATABASE_URL) {
    throw new Error(DB_SKIP_REASON);
  }
  const args = [DATABASE_URL, "-X", "-q", "-A", "-t", "-v", "ON_ERROR_STOP=1"];
  for (const [name, value] of Object.entries(variables)) {
    args.push("-v", `${name}=${value}`);
  }
  return execFileSync("psql", args, { input: script, encoding: "utf8" }).trim();
}

/** The project id of a workspace's `/<org slug>/<project slug>` path. */
export function projectIdOf(base: string): string {
  const [org = "", project = ""] = base.split("/").filter(Boolean);
  const projectId = psql(
    `SELECT p.id FROM projects p JOIN organizations o ON o.id = p.org_id
     WHERE o.slug = :'org' AND p.slug = :'project';`,
    { org, project },
  );
  if (!/^[0-9a-f-]{36}$/.test(projectId)) {
    throw new Error(`No single project for ${base}: ${projectId}`);
  }
  return projectId;
}

export interface SeededIncident {
  id: string;
  title: string;
}

/**
 * An ongoing, API-affecting Anthropic incident that started 20 minutes ago. The table is global
 * (every project sees it), so the caller removes it with `deleteIncident` when done.
 */
export function insertIncident(title: string): SeededIncident {
  const id = randomUUID();
  psql(
    `INSERT INTO provider_incidents
       (id, provider, source, external_id, title, impact, status, api_affected, started_at, url,
        fetched_at)
     VALUES (:'id', 'anthropic', 'statuspage_json', :'external_id', :'title', 'major',
             'investigating', true, now() - interval '20 minutes', :'url', now());`,
    {
      id,
      external_id: `e2e-${id}`,
      title,
      url: `https://status.claude.com/incidents/e2e-${id.slice(0, 8)}`,
    },
  );
  return { id, title };
}

export function deleteIncident(id: string): void {
  psql("DELETE FROM provider_incidents WHERE id = :'id';", { id });
}

/**
 * An open `provider_incident` insight in the project, worded as the detector words it, that cites
 * a status page incident titled `incidentTitle`. Returns the insight id.
 */
export function insertProviderIncidentInsight(
  projectId: string,
  traceIds: readonly string[],
  incidentTitle: string,
): string {
  const id = randomUUID();
  const end = new Date();
  const start = new Date(end.getTime() - 30 * 60_000);
  const evidence = {
    trace_ids: traceIds,
    metrics: {
      org_errors: 24,
      org_share: "0.4000",
      projects_affected: 2,
      incident_id: randomUUID(),
      incident_title: incidentTitle,
      incident_url: "https://status.claude.com/incidents/e2e",
      incident_impact: "major",
    },
    window: { start: start.toISOString(), end: end.toISOString() },
  };
  psql(
    `INSERT INTO insights
       (id, project_id, kind, severity, status, fingerprint, title, summary, failure_layer,
        certainty, evidence, suggested_fix, verification, first_seen_at, last_seen_at, occurrences)
     VALUES (:'id', :'project_id', 'provider_incident', 'warning', 'open', :'fingerprint',
             'anthropic is failing across your organization',
             '24 provider 5xx errors (40.0% of calls) across 2 projects in the last 30 minutes.',
             'provider', 'measured', :'evidence'::jsonb,
             'Fail over to another provider or model while the incident lasts.',
             'The 5xx share drops under 20% in the next 30 minutes.',
             now() - interval '25 minutes', now(), 1);`,
    {
      id,
      project_id: projectId,
      fingerprint: randomBytes(16).toString("hex"),
      evidence: JSON.stringify(evidence),
    },
  );
  return id;
}
