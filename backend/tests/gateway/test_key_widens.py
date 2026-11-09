"""Spending authority changes, independent of HTTP and database state."""

import uuid
from typing import Any

import pytest

from app.db.models import GatewayKey
from app.gateway.key_schemas import GatewayKeyUpdate, widens


@pytest.mark.parametrize(
    ("changes", "expected"),
    [
        ({"rpm_limit": None}, True),
        ({"rpm_limit": 11}, True),
        ({"rpm_limit": 10}, False),
        ({"rpm_limit": 9}, False),
        ({"tpm_limit": None}, True),
        ({"tpm_limit": 101}, True),
        ({"tpm_limit": 90}, False),
        ({"allowed_models": []}, True),
        ({"allowed_models": ["a", "c"]}, True),
        ({"allowed_models": ["a"]}, False),
        ({"allowed_models": ["b", "a"]}, False),
        ({"environment": "production"}, True),
        ({"environment": "staging"}, False),
        ({"route_id": uuid.UUID(int=2)}, True),
        ({"route_id": uuid.UUID(int=1)}, False),
        ({"fault_profile_id": uuid.UUID(int=2)}, False),
        ({"fault_profile_id": None}, False),
        ({"fault_profile_id": uuid.UUID(int=2), "rpm_limit": None}, True),
        ({"name": "new", "default_tags": ["tag"], "cache_ttl_seconds": None}, False),
        ({"password": "private", "code": "123456"}, False),
    ],
)
def test_widens_only_for_more_authority(changes: dict[str, Any], expected: bool) -> None:
    key = GatewayKey(
        route_id=uuid.UUID(int=1),
        environment="staging",
        rpm_limit=10,
        tpm_limit=100,
        allowed_models=["a", "b"],
        fault_profile_id=None,
    )
    update = GatewayKeyUpdate(**changes).changes()
    assert "password" not in update
    assert "code" not in update
    assert widens(key, update) is expected


def test_adding_limits_and_models_to_an_unlimited_key_is_tightening() -> None:
    key = GatewayKey(rpm_limit=None, tpm_limit=None, allowed_models=[], fault_profile_id=None)
    assert not widens(key, {"rpm_limit": 10, "tpm_limit": 100, "allowed_models": ["a"]})
    assert not widens(key, {"rpm_limit": None, "allowed_models": []})
