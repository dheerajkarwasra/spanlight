"""The matrix rows for prompts, scores, the span cost explanation, billing sources, datasets,
annotation queues, the review policy, evaluators, experiments, the playground and exact replay.

One row per route, with the permission that is meant to guard it. The rows for the gateway,
alerts, insights and the rest are in `rows_a.py`.
"""

from app.core.permissions import Permission as P  # noqa: N817 - short, because every row names one
from tests.authz.matrix import Row

PRJ = "/api/v1/projects/{project_id}"
ORG = "/api/v1/orgs/{org_id}"
PROMPT = f"{PRJ}/prompts/{{name:path}}"

PROMPT_ROWS = (
    Row("GET", f"{PRJ}/prompts", P.PROJECT_READ),
    Row("POST", f"{PRJ}/prompts", P.PROMPTS_WRITE),
    Row("GET", PROMPT, P.PROJECT_READ),
    Row("DELETE", PROMPT, P.PROMPTS_WRITE),
    Row("GET", f"{PROMPT}/versions", P.PROJECT_READ),
    Row("POST", f"{PROMPT}/versions", P.PROMPTS_WRITE),
    Row("GET", f"{PROMPT}/versions/{{version}}", P.PROJECT_READ),
    Row("GET", f"{PROMPT}/versions/{{version}}/usage", P.PROJECT_READ),
    Row("PUT", f"{PROMPT}/labels/{{label}}", P.PROMPTS_WRITE),
    Row("DELETE", f"{PROMPT}/labels/{{label}}", P.PROMPTS_WRITE),
    Row("GET", f"{PRJ}/prompt-versions/{{version_id}}", P.PROJECT_READ),
)

SCORE_ROWS = (
    Row("GET", f"{PRJ}/score-configs", P.PROJECT_READ),
    Row("POST", f"{PRJ}/score-configs", P.SCORES_WRITE),
    Row("PATCH", f"{PRJ}/score-configs/{{config_id}}", P.SCORES_WRITE),
    Row("DELETE", f"{PRJ}/score-configs/{{config_id}}", P.SCORES_WRITE),
    Row("GET", f"{PRJ}/traces/{{trace_id}}/scores", P.PROJECT_READ),
    Row("POST", f"{PRJ}/traces/{{trace_id}}/scores", P.SCORES_WRITE),
    Row("DELETE", f"{PRJ}/scores/{{score_id}}", P.SCORES_WRITE),
    Row("POST", f"{PRJ}/score-configs/failure-category", P.SCORES_WRITE),
)

# Bet 1: the lines behind one span's stored cost. API-key readable, like the trace detail.
TRACE_ROWS = (
    Row("GET", f"{PRJ}/traces/{{trace_id}}/spans/{{span_id}}/cost-explanation", P.PROJECT_READ),
    # Phase 2.1: one span with its payloads, for a span trace detail returned without them.
    Row("GET", f"{PRJ}/traces/{{trace_id}}/spans/{{span_id}}", P.PROJECT_READ),
)

# Bet 1: the provider accounts an organization reconciles against. Members read them; admins and
# owners add, edit, rotate and delete them, as these routes hold a provider admin key, and
# refresh them or upload their CSV, as both spend provider calls or replace the bill's lines.
# Members read the reconciliation report and its CSV.
SOURCE = f"{ORG}/billing-sources/{{source_id}}"

BILLING_SOURCE_ROWS = (
    Row("GET", f"{ORG}/billing-sources", P.ORG_READ),
    Row("POST", f"{ORG}/billing-sources", P.BILLING_SOURCES_MANAGE),
    Row("GET", SOURCE, P.ORG_READ),
    Row("PATCH", SOURCE, P.BILLING_SOURCES_MANAGE),
    Row("POST", f"{SOURCE}/rotate", P.BILLING_SOURCES_MANAGE),
    Row("DELETE", SOURCE, P.BILLING_SOURCES_MANAGE),
    Row("POST", f"{SOURCE}/refresh", P.BILLING_SOURCES_MANAGE),
    Row("POST", f"{SOURCE}/upload", P.BILLING_SOURCES_MANAGE),
    Row("GET", f"{SOURCE}/runs", P.ORG_READ),
    Row("GET", f"{SOURCE}/reconciliation", P.ORG_READ),
    Row("GET", f"{SOURCE}/reconciliation.csv", P.ORG_READ),
)

DATASET_ROWS = (
    Row("GET", f"{PRJ}/datasets", P.PROJECT_READ),
    Row("POST", f"{PRJ}/datasets", P.DATASETS_WRITE),
    Row("GET", f"{PRJ}/datasets/{{dataset_id}}", P.PROJECT_READ),
    Row("PATCH", f"{PRJ}/datasets/{{dataset_id}}", P.DATASETS_WRITE),
    Row("DELETE", f"{PRJ}/datasets/{{dataset_id}}", P.DATASETS_WRITE),
    Row("GET", f"{PRJ}/datasets/{{dataset_id}}/items", P.PROJECT_READ),
    Row("POST", f"{PRJ}/datasets/{{dataset_id}}/items", P.DATASETS_WRITE),
    Row("POST", f"{PRJ}/datasets/{{dataset_id}}/items/from-traces", P.DATASETS_WRITE),
    Row("POST", f"{PRJ}/datasets/{{dataset_id}}/items/upload", P.DATASETS_WRITE),
    Row("PATCH", f"{PRJ}/datasets/{{dataset_id}}/items/{{item_id}}", P.DATASETS_WRITE),
    Row("DELETE", f"{PRJ}/datasets/{{dataset_id}}/items/{{item_id}}", P.DATASETS_WRITE),
)

QUEUE = f"{PRJ}/annotation-queues/{{queue_id}}"

ANNOTATION_ROWS = (
    Row("GET", f"{PRJ}/annotation-queues", P.PROJECT_READ),
    Row("POST", f"{PRJ}/annotation-queues", P.ANNOTATIONS_WRITE),
    Row("GET", QUEUE, P.PROJECT_READ),
    Row("DELETE", QUEUE, P.ANNOTATIONS_WRITE),
    Row("GET", f"{QUEUE}/items", P.PROJECT_READ),
    Row("POST", f"{QUEUE}/items", P.ANNOTATIONS_WRITE),
    Row("POST", f"{QUEUE}/claim", P.ANNOTATIONS_WRITE),
    Row("POST", f"{QUEUE}/items/{{item_id}}/complete", P.ANNOTATIONS_WRITE),
    Row("POST", f"{QUEUE}/items/{{item_id}}/skip", P.ANNOTATIONS_WRITE),
    Row("POST", f"{QUEUE}/items/{{item_id}}/release", P.ANNOTATIONS_WRITE),
)

REVIEW_POLICY = f"{PRJ}/review-policy"

# Bet 5: the project's one review policy. The handler answers 404 `NOT_FOUND` while the project
# has none, which the matrix workspace never does, so reading or deleting it may 404.
REVIEW_ROWS = (
    Row("GET", REVIEW_POLICY, P.PROJECT_READ, also=frozenset({404})),
    Row("PUT", REVIEW_POLICY, P.ANNOTATIONS_WRITE),
    Row("DELETE", REVIEW_POLICY, P.ANNOTATIONS_WRITE, also=frozenset({404})),
    Row("POST", f"{REVIEW_POLICY}/runs", P.ANNOTATIONS_WRITE),
    Row("GET", f"{REVIEW_POLICY}/selections", P.PROJECT_READ),
)

EVALUATOR = f"{PRJ}/evaluators/{{evaluator_id}}"

EVALUATOR_ROWS = (
    Row("GET", f"{PRJ}/evaluators", P.PROJECT_READ),
    Row("POST", f"{PRJ}/evaluators", P.EVALUATORS_WRITE),
    Row("GET", EVALUATOR, P.PROJECT_READ),
    Row("PATCH", EVALUATOR, P.EVALUATORS_WRITE),
    Row("DELETE", EVALUATOR, P.EVALUATORS_WRITE),
    Row("GET", f"{EVALUATOR}/runs", P.PROJECT_READ),
    Row("GET", f"{EVALUATOR}/budget", P.PROJECT_READ),
    # Calls a judge model, so it is a write: it spends the organization's provider credit.
    Row("POST", f"{EVALUATOR}/test", P.EVALUATORS_WRITE),
)

EXPERIMENT = f"{PRJ}/experiments/{{experiment_id}}"

EXPERIMENT_ROWS = (
    Row("GET", f"{PRJ}/experiments", P.PROJECT_READ),
    Row("POST", f"{PRJ}/experiments", P.EXPERIMENTS_RUN),
    Row("GET", EXPERIMENT, P.PROJECT_READ),
    Row("GET", f"{EXPERIMENT}/runs", P.PROJECT_READ),
    Row("POST", f"{EXPERIMENT}/runs", P.EXPERIMENTS_RUN),
    Row("GET", f"{EXPERIMENT}/runs/{{run_id}}", P.PROJECT_READ),
    Row("GET", f"{EXPERIMENT}/runs/{{run_id}}/items", P.PROJECT_READ),
    Row("GET", f"{EXPERIMENT}/runs/{{run_a}}/compare/{{run_b}}", P.PROJECT_READ),
    # Runs a prompt against a provider: the same permission as an experiment run.
    Row("POST", f"{PRJ}/playground/runs", P.EXPERIMENTS_RUN),
)

# Bet 4: exact replay. Sending one spends like a playground run; reading is for any member.
REPLAY_ROWS = (
    Row("GET", f"{PRJ}/traces/{{trace_id}}/spans/{{span_id}}/replay-source", P.PROJECT_READ),
    Row("GET", f"{PRJ}/playground/replays", P.PROJECT_READ),
    Row("GET", f"{PRJ}/playground/replays/budget", P.PROJECT_READ),
    Row("POST", f"{PRJ}/playground/replays", P.EXPERIMENTS_RUN),
)

ROWS_B = (
    PROMPT_ROWS
    + SCORE_ROWS
    + TRACE_ROWS
    + BILLING_SOURCE_ROWS
    + DATASET_ROWS
    + ANNOTATION_ROWS
    + EVALUATOR_ROWS
    + EXPERIMENT_ROWS
    + REVIEW_ROWS
    + REPLAY_ROWS
)
