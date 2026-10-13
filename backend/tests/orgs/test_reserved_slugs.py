"""The reserved org slugs match the dashboard's reserved first URL segments."""

import re
from pathlib import Path

from app.services.slugs import RESERVED_ORG_SLUGS

SLUG_MAP = Path(__file__).parents[3] / "frontend/src/features/shell/slug-map.ts"
SET_BODY = re.compile(r"RESERVED_FIRST_SEGMENTS[^=]*=\s*new Set\(\[(.*?)\]\)", re.DOTALL)


def test_reserved_org_slugs_match_the_dashboard_list() -> None:
    match = SET_BODY.search(SLUG_MAP.read_text())
    assert match is not None, "RESERVED_FIRST_SEGMENTS not found in slug-map.ts"
    assert set(re.findall(r'"([^"]+)"', match.group(1))) == RESERVED_ORG_SLUGS
