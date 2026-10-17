"""Spanlight: observability for LLM applications.

Quickstart::

    import spanlight
    from openai import OpenAI

    spanlight.init()  # reads SPANLIGHT_API_KEY / SPANLIGHT_HOST from the environment
    client = spanlight.wrap_openai(OpenAI())

    @spanlight.observe
    def answer(question: str) -> str:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": question}],
        )
        return response.choices[0].message.content or ""
"""

from ._client import SpanContext, Spanlight, update_trace
from ._context import get_current_span, get_current_trace_id
from ._globals import flush, get_client, init, observe, prompts, score, shutdown, span
from ._prompt_client import PromptClient
from ._prompts import CompiledPrompt, Prompt, PromptUnavailableError, no_prompt
from ._span import Span, SpanKind, SpanStatus
from ._templates import InvalidTemplateError, MissingVariablesError, TemplateTooLargeError
from ._version import __version__
from .integrations._openai_batch import record_openai_batch_output
from .integrations.anthropic import wrap_anthropic
from .integrations.openai import wrap_openai

__all__ = [
    "CompiledPrompt",
    "InvalidTemplateError",
    "MissingVariablesError",
    "Prompt",
    "PromptClient",
    "PromptUnavailableError",
    "Span",
    "SpanContext",
    "SpanKind",
    "SpanStatus",
    "Spanlight",
    "TemplateTooLargeError",
    "__version__",
    "flush",
    "get_client",
    "get_current_span",
    "get_current_trace_id",
    "init",
    "no_prompt",
    "observe",
    "prompts",
    "record_openai_batch_output",
    "score",
    "shutdown",
    "span",
    "update_trace",
    "wrap_anthropic",
    "wrap_openai",
]
