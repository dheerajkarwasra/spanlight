"""Zero/one-line instrumentation helpers for agent frameworks and MCP.

Works via optional runtime imports and duck-typing so apps never need extra dependencies
installed unless they actually use that framework.
"""

from __future__ import annotations

import functools
import inspect
import logging
import os
from collections.abc import Callable, Mapping
from typing import Any

from .._context import get_current_span, restore_current_span, set_current_span
from .._globals import get_client
from .._propagation import continue_trace, inject
from .._span import Span, SpanKind

logger = logging.getLogger("spanlight")


def _extract_attr(obj: Any, *names: str, default: Any = None) -> Any:
    for name in names:
        if isinstance(obj, Mapping) and name in obj:
            return obj[name]
        if hasattr(obj, name):
            return getattr(obj, name)
    return default


class SpanlightTracingProcessor:
    """OpenAI Agents SDK ``TracingProcessor`` emitting native Spanlight traces and spans."""

    def __init__(self) -> None:
        self._spans: dict[str, tuple[Span, Any, Span | None]] = {}

    def on_trace_start(self, trace: Any) -> None:
        name = _extract_attr(trace, "name") or "agent.run"
        span = get_client().start_span(str(name), kind="agent")
        trace_id = _extract_attr(trace, "trace_id")
        if trace_id:
            span.set_attribute("agent.framework", "openai_agents")
            span.set_attribute("agent.external_trace_id", str(trace_id))
        group_id = _extract_attr(trace, "group_id")
        if group_id:
            span.trace.update(session_id=str(group_id))
        prev = get_current_span()
        token = set_current_span(span)
        self._spans[f"trace:{id(trace)}"] = (span, token, prev)

    def on_trace_end(self, trace: Any) -> None:
        entry = self._spans.pop(f"trace:{id(trace)}", None)
        if entry is not None:
            span, token, prev = entry
            span.end()
            restore_current_span(token, prev)

    def on_span_start(self, agent_span: Any) -> None:
        data = _extract_attr(agent_span, "span_data")
        kind, name, attrs = _classify_openai_agent_span(data)
        span = get_client().start_span(name, kind=kind)
        span.set_attributes(attrs)
        input_val = _extract_attr(data, "input")
        if input_val is not None:
            span.set_input(input_val)
        prev = get_current_span()
        token = set_current_span(span)
        key = str(_extract_attr(agent_span, "span_id") or id(agent_span))
        self._spans[key] = (span, token, prev)

    def on_span_end(self, agent_span: Any) -> None:
        key = str(_extract_attr(agent_span, "span_id") or id(agent_span))
        entry = self._spans.pop(key, None)
        if entry is None:
            return
        span, token, prev = entry
        data = _extract_attr(agent_span, "span_data")
        output_val = _extract_attr(data, "output", "response")
        if output_val is not None:
            span.set_output(output_val)
        model = _extract_attr(data, "model")
        if isinstance(model, str):
            span.set_model(model)
        usage = _extract_attr(data, "usage")
        if isinstance(usage, Mapping):
            span.set_usage(
                input_tokens=usage.get("input_tokens") or usage.get("prompt_tokens"),
                output_tokens=usage.get("output_tokens") or usage.get("completion_tokens"),
            )
        error = _extract_attr(agent_span, "error")
        if error:
            span.record_error(RuntimeError(str(error)))
        span.end()
        restore_current_span(token, prev)

    def shutdown(self) -> None:
        get_client().flush()

    def force_flush(self) -> None:
        get_client().flush()


def _classify_openai_agent_span(data: Any) -> tuple[SpanKind, str, dict[str, Any]]:
    span_type = str(_extract_attr(data, "type") or type(data).__name__).lower()
    attrs: dict[str, Any] = {"agent.framework": "openai_agents"}
    if "generation" in span_type or "response" in span_type:
        model = _extract_attr(data, "model")
        return "llm", f"chat {model}" if model else "llm", attrs
    if "function" in span_type or "tool" in span_type:
        tool = _extract_attr(data, "name") or "tool"
        attrs["tool.name"] = str(tool)
        return "tool", str(tool), attrs
    if "handoff" in span_type:
        from_a = _extract_attr(data, "from_agent")
        to_a = _extract_attr(data, "to_agent")
        if from_a:
            attrs["agent.handoff.from"] = str(from_a)
        if to_a:
            attrs["agent.handoff.to"] = str(to_a)
        return "agent", f"handoff -> {to_a or 'agent'}", attrs
    name = _extract_attr(data, "name") or span_type or "agent"
    return "agent", str(name), attrs


def instrument_openai_agents(*, register: bool = True) -> SpanlightTracingProcessor:
    """Register (when available) and return a Spanlight OpenAI Agents SDK processor."""
    processor = SpanlightTracingProcessor()
    if register:
        try:
            from agents.tracing import add_trace_processor  # type: ignore[import-not-found]

            add_trace_processor(processor)
        except ImportError:
            logger.debug("openai-agents is not installed; returning unattached processor")
        except Exception:
            logger.warning("Spanlight: could not attach to openai-agents", exc_info=True)
    return processor


class SpanlightCallbackHandler:
    """Duck-typed LangChain / LangGraph callback handler producing Spanlight spans."""

    def __init__(self) -> None:
        self._active: dict[str, tuple[Span, Any, Span | None]] = {}

    def _start(self, run_id: Any, name: str, kind: SpanKind, **attrs: Any) -> Span:
        span = get_client().start_span(name, kind=kind)
        if attrs:
            span.set_attributes(attrs)
        prev = get_current_span()
        token = set_current_span(span)
        self._active[str(run_id)] = (span, token, prev)
        return span

    def _finish(
        self, run_id: Any, *, output: Any = None, error: BaseException | None = None
    ) -> None:
        entry = self._active.pop(str(run_id), None)
        if entry is None:
            return
        span, token, prev = entry
        if output is not None:
            span.set_output(output)
        if error is not None:
            span.record_error(error)
        span.end()
        restore_current_span(token, prev)

    def on_chain_start(
        self,
        serialized: Mapping[str, Any],
        inputs: Any,
        *,
        run_id: Any,
        metadata: Mapping[str, Any] | None = None,
        name: str | None = None,
        **_: Any,
    ) -> None:
        node = metadata.get("langgraph_node") if isinstance(metadata, Mapping) else None
        kind: SpanKind = "agent" if node else "chain"
        label = str(node or name or _extract_attr(serialized, "name", default="chain"))
        span = self._start(run_id, label, kind, **({"langgraph.node": str(node)} if node else {}))
        span.set_input(inputs)

    def on_chain_end(self, outputs: Any, *, run_id: Any, **_: Any) -> None:
        self._finish(run_id, output=outputs)

    def on_chain_error(self, error: BaseException, *, run_id: Any, **_: Any) -> None:
        self._finish(run_id, error=error)

    def on_llm_start(
        self,
        serialized: Mapping[str, Any],
        prompts: Any,
        *,
        run_id: Any,
        invocation_params: Mapping[str, Any] | None = None,
        **_: Any,
    ) -> None:
        model = (
            invocation_params.get("model") or invocation_params.get("model_name")
            if isinstance(invocation_params, Mapping)
            else None
        ) or _extract_attr(serialized, "name")
        span = self._start(run_id, f"chat {model}" if model else "llm", "llm")
        if isinstance(model, str):
            span.set_model(model)
        span.set_input(prompts)

    def on_llm_end(self, response: Any, *, run_id: Any, **_: Any) -> None:
        entry = self._active.get(str(run_id))
        if entry is not None:
            llm_out = _extract_attr(response, "llm_output") or {}
            token_usage = (
                llm_out.get("token_usage") or llm_out.get("usage")
                if isinstance(llm_out, Mapping)
                else None
            )
            if isinstance(token_usage, Mapping):
                entry[0].set_usage(
                    input_tokens=token_usage.get("prompt_tokens")
                    or token_usage.get("input_tokens"),
                    output_tokens=token_usage.get("completion_tokens")
                    or token_usage.get("output_tokens"),
                )
        self._finish(run_id, output=_extract_attr(response, "generations", default=response))

    def on_llm_error(self, error: BaseException, *, run_id: Any, **_: Any) -> None:
        self._finish(run_id, error=error)

    def on_tool_start(
        self,
        serialized: Mapping[str, Any],
        input_str: str,
        *,
        run_id: Any,
        name: str | None = None,
        **_: Any,
    ) -> None:
        tool_name = str(name or _extract_attr(serialized, "name", default="tool"))
        span = self._start(run_id, tool_name, "tool", **{"tool.name": tool_name})
        span.set_input(input_str)

    def on_tool_end(self, output: Any, *, run_id: Any, **_: Any) -> None:
        self._finish(run_id, output=output)

    def on_tool_error(self, error: BaseException, *, run_id: Any, **_: Any) -> None:
        self._finish(run_id, error=error)


def instrument_langgraph(runnable: Any = None) -> Any:
    """Attach a Spanlight callback handler to a compiled LangGraph ``runnable``, or return one."""
    handler = SpanlightCallbackHandler()
    if runnable is not None and hasattr(runnable, "with_config"):
        return runnable.with_config({"callbacks": [handler]})
    return handler


def instrument_pydantic_ai(
    *, agent: Any = None, host: str | None = None, api_key: str | None = None
) -> dict[str, str]:
    """Configure OTLP env settings for Pydantic AI and enable instrumentation when installed."""
    client = get_client()
    base = (host or client.config.host).rstrip("/")
    token = api_key or client.config.api_key or ""
    endpoint = f"{base}/v1/otlp/traces"
    os.environ.setdefault("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT", endpoint)
    if token:
        os.environ.setdefault("OTEL_EXPORTER_OTLP_HEADERS", f"Authorization=Bearer%20{token}")
    if agent is not None:
        agent.instrument = True
    else:
        try:
            from pydantic_ai import Agent  # type: ignore[import-not-found]

            Agent.instrument_all()
        except ImportError:
            logger.debug("pydantic_ai is not installed; OTLP env settings populated only")
        except Exception:
            logger.warning("Spanlight: could not call Agent.instrument_all()", exc_info=True)
    return {"OTEL_EXPORTER_OTLP_TRACES_ENDPOINT": endpoint}


def instrument_mcp(target: Any) -> Any:
    """Wrap an MCP client/server ``call_tool`` to emit ``tool`` spans and trace context."""
    call_tool = getattr(target, "call_tool", None)
    if callable(call_tool) and not getattr(call_tool, "_spanlight_wrapped", False):
        if inspect.iscoroutinefunction(call_tool):

            @functools.wraps(call_tool)
            async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                name = str(args[0] if args else kwargs.get("name", "mcp.tool"))
                meta = kwargs.get("_meta") or kwargs.get("meta")
                with (
                    continue_trace(meta) if meta is not None else _noop_ctx(),
                    get_client().span(
                        name,
                        kind="tool",
                        input={"args": args[1:], "kwargs": kwargs},
                        attributes={"mcp.tool.name": name, "rpc.system": "mcp"},
                    ) as sp,
                ):
                    if isinstance(kwargs.get("params"), dict):
                        kwargs["params"]["_meta"] = inject(kwargs["params"].get("_meta"))
                    result = await call_tool(*args, **kwargs)
                    sp.set_output(result)
                    return result

            async_wrapper._spanlight_wrapped = True  # type: ignore[attr-defined]
            target.call_tool = async_wrapper
        else:

            @functools.wraps(call_tool)
            def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
                name = str(args[0] if args else kwargs.get("name", "mcp.tool"))
                meta = kwargs.get("_meta") or kwargs.get("meta")
                with (
                    continue_trace(meta) if meta is not None else _noop_ctx(),
                    get_client().span(
                        name,
                        kind="tool",
                        input={"args": args[1:], "kwargs": kwargs},
                        attributes={"mcp.tool.name": name, "rpc.system": "mcp"},
                    ) as sp,
                ):
                    if isinstance(kwargs.get("params"), dict):
                        kwargs["params"]["_meta"] = inject(kwargs["params"].get("_meta"))
                    result = call_tool(*args, **kwargs)
                    sp.set_output(result)
                    return result

            sync_wrapper._spanlight_wrapped = True  # type: ignore[attr-defined]
            target.call_tool = sync_wrapper
    return target


class _noop_ctx:
    def __enter__(self) -> None:
        return None

    def __exit__(self, *_: Any) -> None:
        return None


def wrap_agent_tool(name: str | None = None) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Decorator marking a tool function with `kind="tool"`."""

    def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
        span_name = name or fn.__name__

        @functools.wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            with get_client().span(
                span_name, kind="tool", input={"args": args, "kwargs": kwargs}
            ) as sp:
                result = fn(*args, **kwargs)
                sp.set_output(result)
                return result

        return wrapper

    return decorator
