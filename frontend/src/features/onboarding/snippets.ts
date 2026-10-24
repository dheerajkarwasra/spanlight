/**
 * Integration snippets for the onboarding "Connect" step. Pure string
 * builders so they can be unit tested and stay in sync with the Python SDK
 * (`sdks/python`, import `spanlight`) and the ingestion contract.
 */

import {
  GATEWAY_MODE_NOTES,
  gatewayAnthropicBaseUrl,
  gatewayBaseUrl,
  gatewaySnippetHost,
} from "@/features/gateway";
import type { GatewayMode } from "@/lib/api";

export const API_KEY_PLACEHOLDER = "<YOUR_API_KEY>";
export const DEFAULT_ENVIRONMENT = "production";

export const GATEWAY_KEY_PLACEHOLDER = "<YOUR_GATEWAY_KEY>";

export type SnippetId = "python" | "openai" | "anthropic" | "otel" | "curl" | "gateway";
export type SnippetLanguage = "shell" | "python";

export interface SnippetBlock {
  /** Short heading used in the block's accessible name, e.g. "Install". */
  title: string;
  /** Shown in mono in the code card header, e.g. "quickstart.py" or "terminal". */
  filename: string;
  language: SnippetLanguage;
  code: string;
}

export interface Snippet {
  id: SnippetId;
  label: string;
  description: string;
  blocks: SnippetBlock[];
}

const TERMINAL = "terminal";
const QUICKSTART = "quickstart.py";

export interface SnippetOptions {
  /** The real secret when one was just created, otherwise null for the placeholder. */
  apiKey: string | null;
  /** Origin of this Spanlight instance, e.g. "https://spanlight.example.com". */
  host: string;
  environment?: string;
  /** `/me`'s gateway mode; a gateway this origin doesn't serve gets a placeholder host. */
  gatewayMode?: GatewayMode;
}

function normalizeHost(host: string): string {
  return host.replace(/\/+$/, "");
}

function initCall(apiKey: string, host: string, environment: string): string {
  return `spanlight.init(
    api_key="${apiKey}",
    host="${host}",
    environment="${environment}",
)`;
}

function pythonSnippet(apiKey: string, host: string, environment: string): Snippet {
  return {
    id: "python",
    label: "Python SDK",
    description:
      "Trace any function with the @observe() decorator. Nested calls become child spans.",
    blocks: [
      {
        title: "Install",
        filename: TERMINAL,
        language: "shell",
        code: "pip install spanlight",
      },
      {
        title: "Instrument",
        filename: QUICKSTART,
        language: "python",
        code: `import spanlight
from spanlight import observe

${initCall(apiKey, host, environment)}


@observe()
def answer(question: str) -> str:
    # Your LLM calls, retrieval and tools go here.
    return f"You asked: {question}"


answer("What does Spanlight record?")

# Scripts and serverless handlers: send buffered spans before exiting.
spanlight.flush()`,
      },
    ],
  };
}

function openAiSnippet(apiKey: string, host: string, environment: string): Snippet {
  return {
    id: "openai",
    label: "OpenAI",
    description:
      "Wrap the client once. Every chat.completions and responses call is traced with tokens, cost and latency.",
    blocks: [
      {
        title: "Install",
        filename: TERMINAL,
        language: "shell",
        code: 'pip install "spanlight[openai]"',
      },
      {
        title: "Instrument",
        filename: QUICKSTART,
        language: "python",
        code: `import spanlight
from spanlight import wrap_openai
from openai import OpenAI

${initCall(apiKey, host, environment)}

client = wrap_openai(OpenAI())

response = client.chat.completions.create(
    model="gpt-4o-mini",
    messages=[{"role": "user", "content": "Say hello"}],
)
print(response.choices[0].message.content)

spanlight.flush()`,
      },
    ],
  };
}

function anthropicSnippet(apiKey: string, host: string, environment: string): Snippet {
  return {
    id: "anthropic",
    label: "Anthropic",
    description:
      "Wrap the client once. messages.create and messages.stream are traced, including streaming.",
    blocks: [
      {
        title: "Install",
        filename: TERMINAL,
        language: "shell",
        code: 'pip install "spanlight[anthropic]"',
      },
      {
        title: "Instrument",
        filename: QUICKSTART,
        language: "python",
        code: `import spanlight
from anthropic import Anthropic
from spanlight import wrap_anthropic

${initCall(apiKey, host, environment)}

client = wrap_anthropic(Anthropic())

message = client.messages.create(
    model="claude-haiku-4-5",
    max_tokens=256,
    messages=[{"role": "user", "content": "Say hello"}],
)
print(message.content[0].text)

spanlight.flush()`,
      },
    ],
  };
}

function otelSnippet(apiKey: string, host: string, environment: string): Snippet {
  return {
    id: "otel",
    label: "OpenTelemetry",
    description:
      "Point any OTLP/HTTP exporter at Spanlight. Spans with gen_ai.* attributes are treated as LLM calls.",
    blocks: [
      {
        title: "Environment",
        filename: TERMINAL,
        language: "shell",
        // The headers variable uses W3C baggage encoding, so the space after "Bearer" is %20.
        code: `export OTEL_EXPORTER_OTLP_TRACES_ENDPOINT="${host}/v1/otlp/traces"
export OTEL_EXPORTER_OTLP_TRACES_HEADERS="Authorization=Bearer%20${apiKey}"
export OTEL_EXPORTER_OTLP_TRACES_PROTOCOL="http/protobuf"
export OTEL_RESOURCE_ATTRIBUTES="deployment.environment.name=${environment}"
export OTEL_SERVICE_NAME="my-llm-app"`,
      },
    ],
  };
}

function curlSnippet(apiKey: string, host: string, environment: string): Snippet {
  return {
    id: "curl",
    label: "curl",
    description: "Send one LLM span over plain HTTP to check the connection end to end.",
    blocks: [
      {
        title: "Send a test span",
        filename: TERMINAL,
        language: "shell",
        code: `TRACE_ID=$(openssl rand -hex 16)
SPAN_ID=$(openssl rand -hex 8)
NOW=$(date -u +%Y-%m-%dT%H:%M:%SZ)

curl -sS -X POST "${host}/v1/traces" \\
  -H "Authorization: Bearer ${apiKey}" \\
  -H "Content-Type: application/json" \\
  --data-binary @- <<EOF
{
  "spans": [{
    "trace_id": "$TRACE_ID",
    "span_id": "$SPAN_ID",
    "parent_span_id": null,
    "name": "chat gpt-4o-mini",
    "kind": "llm",
    "status": "ok",
    "start_time": "$NOW",
    "end_time": "$NOW",
    "provider": "openai",
    "model": "gpt-4o-mini",
    "usage": {"input_tokens": 12, "output_tokens": 40, "cached_tokens": 0},
    "input": {"messages": [{"role": "user", "content": "Say hello"}]},
    "output": {"role": "assistant", "content": "Hello"},
    "trace": {"name": "hello-world", "environment": "${environment}"}
  }]
}
EOF`,
      },
    ],
  };
}

/**
 * "No SDK": point an existing provider client at the gateway. The key is a gateway key, never the
 * ingest API key, so it is always the placeholder; the user creates one under Gateway › Credentials.
 */
function gatewaySnippet(origin: string, mode: GatewayMode): Snippet {
  const host = gatewaySnippetHost(mode, origin);
  const openAiBase = gatewayBaseUrl(host);
  const anthropicBase = gatewayAnthropicBaseUrl(host);
  const steps = "Add a provider credential, then create a gateway key.";
  return {
    id: "gateway",
    label: "No SDK",
    description: mode === "embedded" ? steps : `${GATEWAY_MODE_NOTES[mode]} ${steps}`,
    blocks: [
      {
        title: "OpenAI SDK",
        filename: "openai_sdk.py",
        language: "python",
        code: `from openai import OpenAI

client = OpenAI(
    base_url="${openAiBase}",
    api_key="${GATEWAY_KEY_PLACEHOLDER}",
)

response = client.chat.completions.create(
    model="gpt-4.1-mini",
    messages=[{"role": "user", "content": "Say hello"}],
)
print(response.choices[0].message.content)`,
      },
      {
        title: "Anthropic SDK",
        filename: "anthropic_sdk.py",
        language: "python",
        code: `from anthropic import Anthropic

client = Anthropic(
    base_url="${anthropicBase}",
    api_key="${GATEWAY_KEY_PLACEHOLDER}",
)

message = client.messages.create(
    model="claude-haiku-4-5",
    max_tokens=256,
    messages=[{"role": "user", "content": "Say hello"}],
)
print(message.content[0].text)`,
      },
      {
        title: "curl",
        filename: "curl",
        language: "shell",
        code: `curl -sS -X POST "${openAiBase}/chat/completions" \\
  -H "Authorization: Bearer ${GATEWAY_KEY_PLACEHOLDER}" \\
  -H "Content-Type: application/json" \\
  -d '{"model": "gpt-4.1-mini", "messages": [{"role": "user", "content": "Say hello"}]}'`,
      },
    ],
  };
}

/** All snippets, in tab order, with the key and host filled in. */
export function buildSnippets({
  apiKey,
  host,
  environment = DEFAULT_ENVIRONMENT,
  gatewayMode = "embedded",
}: SnippetOptions): Snippet[] {
  const key = apiKey ?? API_KEY_PLACEHOLDER;
  const origin = normalizeHost(host);
  return [
    pythonSnippet(key, origin, environment),
    openAiSnippet(key, origin, environment),
    anthropicSnippet(key, origin, environment),
    otelSnippet(key, origin, environment),
    curlSnippet(key, origin, environment),
    gatewaySnippet(origin, gatewayMode),
  ];
}
