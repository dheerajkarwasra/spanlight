"""The matrix rows for prompts, scores, datasets, annotation queues, evaluators, experiments and
the playground.

One row per route, with the permission that is meant to guard it. The rows for the gateway,
alerts, insights and the rest are in `rows_a.py`.
"""

from app.core.permissions import Permission as P  # noqa: N817 - short, because every row names one
from tests.authz.matrix import Row

PRJ = "/api/v1/projects/{project_id}"
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

ROWS_B = (
    PROMPT_ROWS + SCORE_ROWS + DATASET_ROWS + ANNOTATION_ROWS + EVALUATOR_ROWS + EXPERIMENT_ROWS
)
