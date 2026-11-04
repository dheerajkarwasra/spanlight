"""Prompt compilation contracts exercised through the public SDK without a provider."""

import pytest

from spanlight import MissingVariablesError, PromptClient, PromptUnavailableError


@pytest.fixture
def prompts():
    client = PromptClient(
        base_url="http://localhost/v1/prompts", api_key=None, timeout=1, cache_ttl=0
    )
    yield client
    client.close()


def test_spaces_and_tabs_inside_placeholders_are_supported(prompts):
    prompt = prompts.get("greet", fallback="{{ name }}|{{\tname\t}}")

    compiled = prompt.compile(name="Ada")

    assert compiled.text == "Ada|Ada"
    assert compiled.variables_used == ("name",)


def test_variables_keep_first_appearance_order_and_report_unused_names(prompts):
    prompt = prompts.get("greet", fallback="{{zebra}} {{alpha}} {{zebra}}")

    compiled = prompt.compile(zebra="Z", alpha="A", unused_z="z", unused_a="a")

    assert compiled.text == "Z A Z"
    assert compiled.variables_used == ("zebra", "alpha")
    assert compiled.unused_variables == ("unused_a", "unused_z")


def test_missing_variables_are_sorted_and_deduplicated(prompts):
    prompt = prompts.get("greet", fallback="{{zebra}} {{alpha}} {{zebra}}")

    with pytest.raises(MissingVariablesError) as error:
        prompt.compile()

    assert error.value.names == ("alpha", "zebra")


@pytest.mark.parametrize("template", ["{{name", "{{bad-name}}", "{{1name}}", "{{}}", "{{\rname}}"])
def test_invalid_placeholders_raise_the_documented_fallback_error(prompts, template):
    with pytest.raises(PromptUnavailableError, match="fallback is invalid"):
        prompts.get("greet", fallback=template)


@pytest.mark.parametrize(
    "messages",
    [[], [{"role": "tool", "content": "text"}], [{"role": "user", "content": 42}], [{}]],
)
def test_invalid_chat_messages_raise_the_documented_fallback_error(prompts, messages):
    with pytest.raises(PromptUnavailableError, match="fallback is invalid"):
        prompts.get("greet", fallback=messages)


def test_chat_accepts_fifty_messages_and_keeps_roles_and_order(prompts):
    messages = [{"role": "system", "content": "Instruction"}] + [
        {"role": "user", "content": "{{name}}"}
    ] * 49
    prompt = prompts.get("greet", fallback=messages)

    compiled = prompt.compile(name="Ada")

    assert (
        compiled.messages
        == [{"role": "system", "content": "Instruction"}]
        + [{"role": "user", "content": "Ada"}] * 49
    )
    assert compiled.text is None


def test_chat_rejects_fifty_one_messages(prompts):
    messages = [{"role": "user", "content": "hello"}] * 51

    with pytest.raises(PromptUnavailableError, match="fallback is invalid"):
        prompts.get("greet", fallback=messages)


def test_text_template_accepts_exactly_sixty_four_kib_of_utf8(prompts):
    text = "é" * 32768

    compiled = prompts.get("greet", fallback=text).compile()

    assert compiled.text == text


def test_text_template_rejects_more_than_sixty_four_kib_of_utf8(prompts):
    with pytest.raises(PromptUnavailableError, match="fallback is invalid"):
        prompts.get("greet", fallback="é" * 32768 + "a")
