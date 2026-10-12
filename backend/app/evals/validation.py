"""The checks an evaluator config must pass beyond its shape, against the project and the org.

* every kind: an `exact_match` or `levenshtein` heuristic compares against an expected output,
  which online traces never have, so it cannot sample (`RULE_NEEDS_EXPECTED_OUTPUT`);
* a judge needs a positive monthly budget (`BUDGET_REQUIRED`), a chat prompt version of the
  project whose variables are among `input`, `output` and `expected_output`
  (`JUDGE_TEMPLATE_VARIABLES`), an Anthropic or OpenAI credential of the organization, and a
  model with a price, without which the budget could not be enforced (`EVALUATOR_MODEL_UNPRICED`);
* a webhook names a `webhook` alert channel of the organization (`CHANNEL_KIND`).

A prompt version or credential the caller cannot see is `EvaluatorReferenceNotFoundError` (404),
never a hint that it exists elsewhere.
"""

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AlertChannelKind, PromptKind, ProviderKind
from app.evals import queries
from app.evals.budget import estimate_call_cost
from app.evals.config import HeuristicEvaluatorConfig, JudgeConfig, WebhookEvaluatorConfig
from app.evals.errors import EvaluatorConfigError, EvaluatorReferenceNotFoundError
from app.gateway import service as gateway_service
from app.pricing.cost import load_price_book
from app.prompts import service as prompt_service

JUDGE_VARIABLES = frozenset({"input", "output", "expected_output"})
JUDGE_PROVIDERS = frozenset({ProviderKind.ANTHROPIC, ProviderKind.OPENAI})

AnyConfig = HeuristicEvaluatorConfig | JudgeConfig | WebhookEvaluatorConfig


async def check_config(
    db: AsyncSession,
    *,
    org_id: uuid.UUID,
    project_id: uuid.UUID,
    config: AnyConfig,
    sampling_rate: Decimal,
    monthly_budget_usd: Decimal | None,
    now: datetime,
) -> None:
    """Raise `EvaluatorConfigError` or `EvaluatorReferenceNotFoundError` when the evaluator
    cannot be stored as configured."""
    if isinstance(config, HeuristicEvaluatorConfig):
        check_heuristic(config, sampling_rate)
    elif isinstance(config, JudgeConfig):
        await _check_judge(db, org_id, project_id, config, monthly_budget_usd, now)
    else:
        await _check_webhook(db, org_id, config)


def check_heuristic(config: HeuristicEvaluatorConfig, sampling_rate: Decimal) -> None:
    """Pure: a rule that needs an expected output cannot sample live traces."""
    if config.needs_expected_output and sampling_rate > 0:
        raise EvaluatorConfigError(
            "RULE_NEEDS_EXPECTED_OUTPUT",
            "This rule compares against an expected output, which live traces do not have. "
            "Set sampling_rate to 0 and use it in experiments.",
            "sampling_rate",
        )


async def _check_judge(
    db: AsyncSession,
    org_id: uuid.UUID,
    project_id: uuid.UUID,
    config: JudgeConfig,
    monthly_budget_usd: Decimal | None,
    now: datetime,
) -> None:
    if monthly_budget_usd is None or monthly_budget_usd <= 0:
        raise EvaluatorConfigError(
            "BUDGET_REQUIRED",
            "A judge spends money unattended, so it needs a positive monthly budget.",
            "monthly_budget_usd",
        )
    # Holds the prompt until the evaluator is stored, so a concurrent delete cannot slip past
    # its in-use check.
    version = await prompt_service.project_version(
        db, project_id, config.prompt_version_id, hold=True
    )
    if version is None:
        raise EvaluatorReferenceNotFoundError
    if version.kind != PromptKind.CHAT or not set(version.variables) <= JUDGE_VARIABLES:
        raise EvaluatorConfigError(
            "JUDGE_TEMPLATE_VARIABLES",
            "A judge prompt must be a chat template using only the variables input, output "
            "and expected_output.",
            "config.prompt_version_id",
        )
    credential = await gateway_service.org_credential(db, org_id, config.credential_id)
    if credential is None:
        raise EvaluatorReferenceNotFoundError
    if credential.provider not in JUDGE_PROVIDERS:
        raise EvaluatorConfigError(
            "VALIDATION_ERROR",
            "A judge calls Anthropic or OpenAI; pick a credential of one of them.",
            "config.credential_id",
        )
    prices = await load_price_book(db, org_id)
    estimate = estimate_call_cost(
        config.model, 1, config.max_tokens, prices, provider=credential.provider.value, at=now
    )
    # A zero price (an organization's override, say) would make every call free to the budget.
    if estimate is None or estimate <= 0:
        raise EvaluatorConfigError(
            "EVALUATOR_MODEL_UNPRICED",
            f"The model {config.model} has no price, so the judge's budget cannot be enforced.",
            "config.model",
        )


async def _check_webhook(
    db: AsyncSession, org_id: uuid.UUID, config: WebhookEvaluatorConfig
) -> None:
    channel = await queries.org_channel(db, org_id, config.channel_id)
    if channel is None or channel.kind != AlertChannelKind.WEBHOOK:
        raise EvaluatorConfigError(
            "CHANNEL_KIND",
            "A webhook evaluator needs a webhook channel of this organization.",
            "config.channel_id",
        )
