"""Whether a collected row meets its scenario's expectation, on hand-made rows."""

from typing import Any

from accuracy.doctor.collect import SEEDED_INCIDENT_TITLE, expectation_met
from accuracy.doctor.scenario_types import Expectation

SPEND_CAP = Expectation(rate_limit_kind="spend_cap")
CITED = Expectation(incident_cited=True)
TITLE = "Spanlight Doctor harness fixture (ab12cd34)"


def _row(fired: bool = True, **fields: Any) -> dict[str, Any]:
    return {"fired": fired, **fields}


def test_nothing_to_check_without_an_expectation_or_a_finding() -> None:
    assert expectation_met(None, _row()) is None
    assert expectation_met(SPEND_CAP, _row(fired=False)) is None


def test_spend_cap_is_met_only_when_every_evidence_span_reads_spend_cap() -> None:
    assert expectation_met(SPEND_CAP, _row(rate_limit_kinds=["spend_cap"]))
    assert not expectation_met(SPEND_CAP, _row(rate_limit_kinds=["requests", "spend_cap"]))
    assert not expectation_met(SPEND_CAP, _row(rate_limit_kinds=[None]))
    assert not expectation_met(SPEND_CAP, _row())


def test_citation_must_name_the_incident_the_run_seeded() -> None:
    facts = {SEEDED_INCIDENT_TITLE: TITLE}
    cited = [{"id": "0b7c", "title": TITLE}]
    assert expectation_met(CITED, _row(run_facts=facts, incident_citations=cited))
    other = [{"id": "9f1e", "title": "Elevated errors on Claude Opus"}]
    assert not expectation_met(CITED, _row(run_facts=facts, incident_citations=other))
    uncited = [{"id": None, "title": None}]
    assert not expectation_met(CITED, _row(run_facts=facts, incident_citations=uncited))
    assert not expectation_met(CITED, _row(run_facts={}, incident_citations=cited))
