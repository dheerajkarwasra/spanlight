"""Traceloop mapper: the `traceloop` rows of spec §4.3 (workflow, task, agent and tool spans)."""

from app.db.models.telemetry import SpanKind
from app.ingest.conventions.traceloop import map_traceloop

WORKFLOW = {
    "traceloop.span.kind": "workflow",
    "traceloop.workflow.name": "support_bot",
    "traceloop.entity.name": "answer",
    "traceloop.entity.input": '{"question": "hi"}',
    "traceloop.entity.output": '["ok", 2]',
    "traceloop.association.properties.User_Id": "u-1",
    "traceloop.association.properties.tenant": 7,
    "gen_ai.system": "openai",
}


def test_entity_input_json_is_parsed() -> None:
    fields = map_traceloop(WORKFLOW)
    assert fields.input == {"question": "hi"}
    assert fields.output == ["ok", 2]
    assert {"traceloop.entity.input", "traceloop.entity.output"} <= fields.consumed_keys


def test_entity_input_text_stays_text() -> None:
    fields = map_traceloop({"traceloop.span.kind": "task", "traceloop.entity.input": "plain text"})
    assert fields.input == "plain text"
    assert fields.output is None
    assert fields.consumed_keys == frozenset({"traceloop.entity.input"})


def test_kind_and_operation() -> None:
    assert map_traceloop(WORKFLOW).kind is SpanKind.CHAIN
    assert map_traceloop(WORKFLOW).operation == "workflow"
    tool = map_traceloop({"traceloop.span.kind": " Tool "})
    assert tool.kind is SpanKind.TOOL
    assert tool.operation == " Tool "
    unknown = map_traceloop({"traceloop.span.kind": "unknown"})
    assert unknown.kind is SpanKind.OTHER
    assert unknown.operation == "unknown"


def test_association_properties_become_metadata() -> None:
    fields = map_traceloop(WORKFLOW)
    assert fields.metadata == {"user_id": "u-1", "tenant": 7}
    assert {
        "traceloop.association.properties.User_Id",
        "traceloop.association.properties.tenant",
    } <= fields.consumed_keys


def test_workflow_name_beats_entity_name_for_trace_name() -> None:
    assert map_traceloop(WORKFLOW).trace_name == "support_bot"
    entity_only = {"traceloop.span.kind": "task", "traceloop.entity.name": "answer"}
    assert map_traceloop(entity_only).trace_name == "answer"
    assert map_traceloop({"traceloop.span.kind": "task"}).trace_name is None


def test_no_provider_model_or_usage() -> None:
    fields = map_traceloop({**WORKFLOW, "gen_ai.request.model": "gpt-4.1"})
    assert fields.provider is None
    assert fields.model is None
    assert fields.usage.input_tokens is None
    assert fields.usage.output_tokens is None
    assert "gen_ai.system" not in fields.consumed_keys
