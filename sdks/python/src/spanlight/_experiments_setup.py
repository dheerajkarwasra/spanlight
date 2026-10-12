"""Find or create the experiment a CI run belongs to."""

from __future__ import annotations

import hashlib
import json
import uuid
from decimal import Decimal
from typing import Any

from ._experiments_api import Api, ExperimentError, ResolvedPrompt, find_by_name

# The server's limit on an experiment name.
_MAX_NAME = 200


def normalize_credential_id(value: str) -> str:
    """The credential id in canonical lower-case form, so a pasted upper-case id still matches."""
    try:
        return str(uuid.UUID(value.strip()))
    except ValueError:
        raise ExperimentError("--credential-id is not a UUID") from None


def experiment_name(
    dataset: str, prompt: str, evaluator_names: list[str], credential_id: str, model: str
) -> str:
    """``ci/<dataset>/<prompt>/<8 hex>``; the digest follows the evaluators, credential and model.

    An experiment cannot be edited, so a gate that changes one of these gets its own experiment
    instead of silently running under the old settings.
    """
    settings = json.dumps(
        [sorted(set(evaluator_names)), credential_id, model], separators=(",", ":")
    )
    digest = hashlib.sha256(settings.encode()).hexdigest()[:8]
    name = f"ci/{dataset}/{prompt}/{digest}"
    if len(name) > _MAX_NAME:
        raise ExperimentError(
            f"the experiment name would be {len(name)} characters; the limit is {_MAX_NAME}. "
            "Use a shorter dataset or prompt name"
        )
    return name


def experiment_for(
    api: Api,
    dataset: dict[str, Any],
    version: ResolvedPrompt,
    evaluator_names: list[str],
    evaluator_ids: list[str],
    *,
    credential_id: str,
    model: str,
    max_cost_usd: Decimal | None,
) -> dict[str, Any]:
    """The experiment for this dataset, prompt and settings, created on first use.

    Runs of one experiment stay comparable. It is created with the resolved version as its base
    target; every run then names the version it wants through ``target_override``, so a label that
    moved runs the new version. ``max_cost_usd`` applies only when the experiment is created.
    """
    name = experiment_name(dataset["name"], version.name, evaluator_names, credential_id, model)
    path = api.project("/experiments")
    existing = find_by_name(api, path, name)
    if existing is not None:
        return existing
    config: dict[str, Any] = {
        "target": {
            "kind": "prompt_version",
            "prompt_version_id": version.version_id,
            "credential_id": credential_id,
            "model": model,
        }
    }
    if max_cost_usd is not None:
        config["max_cost_usd"] = f"{max_cost_usd:f}"
    body = {
        "name": name,
        "dataset_id": dataset["id"],
        "config": config,
        "evaluator_ids": evaluator_ids,
    }
    created: dict[str, Any] = api.request("POST", path, body=body)
    return created
