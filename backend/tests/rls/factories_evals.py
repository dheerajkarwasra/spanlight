"""Row factories for the prompt, score, dataset, annotation, evaluator and experiment tables."""

from tests.rls.row_spec import HEX32, SLUG, RowFactory, parent

EVALS = (
    RowFactory(
        table="prompts",
        sql=(
            "INSERT INTO prompts (id, project_id, name) "
            f"VALUES (gen_random_uuid(), :project_id, {SLUG}) RETURNING *"
        ),
        touch="updated_at",
    ),
    RowFactory(
        table="prompt_versions",
        sql=(
            "INSERT INTO prompt_versions (id, prompt_id, version, kind, template) "
            "VALUES (gen_random_uuid(), :prompt_id, 1, 'text', "
            """'{"kind": "text", "text": "hi"}') RETURNING *"""
        ),
        # The immutability trigger lets exactly this no-op through (`created_by` stays NULL).
        touch="created_by",
        parents=(parent("prompts", prompt_id="id"),),
    ),
    RowFactory(
        table="prompt_labels",
        sql=(
            "INSERT INTO prompt_labels (prompt_id, label, version_id) "
            f"VALUES (:prompt_id, {SLUG}, :version_id) RETURNING *"
        ),
        touch="updated_at",
        parents=(parent("prompt_versions", prompt_id="prompt_id", version_id="id"),),
    ),
    RowFactory(
        table="score_configs",
        sql=(
            "INSERT INTO score_configs (id, project_id, name, data_type) "
            f"VALUES (gen_random_uuid(), :project_id, {SLUG}, 'boolean') RETURNING *"
        ),
        touch="implicit",
    ),
    RowFactory(
        table="scores",
        sql=(
            "INSERT INTO scores (id, project_id, trace_id, name, data_type, source, value) "
            f"VALUES (gen_random_uuid(), :project_id, {HEX32}, 'quality', 'boolean', 'api', 1) "
            "RETURNING *"
        ),
        touch="name",
    ),
    RowFactory(
        table="datasets",
        sql=(
            "INSERT INTO datasets (id, project_id, name) "
            f"VALUES (gen_random_uuid(), :project_id, {SLUG}) RETURNING *"
        ),
        touch="updated_at",
    ),
    RowFactory(
        table="dataset_items",
        sql=(
            "INSERT INTO dataset_items (id, dataset_id, input) "
            "VALUES (gen_random_uuid(), :dataset_id, '{}') RETURNING *"
        ),
        touch="metadata",
        parents=(parent("datasets", dataset_id="id"),),
    ),
    RowFactory(
        table="annotation_queues",
        sql=(
            "INSERT INTO annotation_queues (id, project_id, name, score_config_ids) "
            f"VALUES (gen_random_uuid(), :project_id, {SLUG}, ARRAY[gen_random_uuid()]) "
            "RETURNING *"
        ),
        touch="name",
    ),
    RowFactory(
        table="annotation_queue_items",
        sql=(
            "INSERT INTO annotation_queue_items (id, queue_id, trace_id) "
            f"VALUES (gen_random_uuid(), :queue_id, {HEX32}) RETURNING *"
        ),
        touch="status",
        parents=(parent("annotation_queues", queue_id="id"),),
    ),
    RowFactory(
        table="evaluators",
        sql=(
            "INSERT INTO evaluators (id, project_id, name, kind, config) "
            f"VALUES (gen_random_uuid(), :project_id, {SLUG}, 'heuristic', "
            """'{"kind": "heuristic"}') RETURNING *"""
        ),
        touch="enabled",
    ),
    RowFactory(
        table="evaluator_runs",
        sql=(
            "INSERT INTO evaluator_runs (id, evaluator_id, trace_id) "
            f"VALUES (gen_random_uuid(), :evaluator_id, {HEX32}) RETURNING *"
        ),
        touch="status",
        parents=(parent("evaluators", evaluator_id="id"),),
    ),
    RowFactory(
        table="experiments",
        sql=(
            "INSERT INTO experiments (id, project_id, name, dataset_id, config) "
            f"VALUES (gen_random_uuid(), :project_id, {SLUG}, :dataset_id, '{{}}') RETURNING *"
        ),
        touch="config",
        parents=(parent("datasets", dataset_id="id"),),
    ),
    RowFactory(
        table="experiment_runs",
        sql=(
            "INSERT INTO experiment_runs (id, experiment_id, number, target) "
            "VALUES (gen_random_uuid(), :experiment_id, 1, '{}') RETURNING *"
        ),
        touch="target",
        parents=(parent("experiments", experiment_id="id"),),
    ),
    RowFactory(
        table="experiment_items",
        sql=(
            "INSERT INTO experiment_items (run_id, dataset_item_id) "
            "VALUES (:run_id, :dataset_item_id) RETURNING *"
        ),
        touch="scores",
        parents=(
            parent("experiment_runs", run_id="id"),
            parent("dataset_items", dataset_item_id="id"),
        ),
    ),
)
