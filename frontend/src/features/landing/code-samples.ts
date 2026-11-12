/**
 * Integration snippets for the "Start" code card. Lines are pre-split into segments (Figma "Code
 * card": grey code with the Spanlight calls in white) so the card renders as designed;
 * `codeToText` rebuilds the plain text for copying.
 *
 * Snippets use the real Python SDK API (`sdks/python`, imported as `spanlight`); the Python one
 * is the README quickstart.
 */

export type CodeTone = "plain" | "muted" | "accent";

export interface CodeSegment {
  text: string;
  tone: CodeTone;
}

export type CodeLine = readonly CodeSegment[];

export type CodeSampleId = "python" | "otlp" | "gateway";

export interface CodeSample {
  id: CodeSampleId;
  /** Option title, e.g. "Python SDK". */
  label: string;
  /** The sentence under the option title. */
  summary: string;
  /** Accessible name of the code region. */
  description: string;
  lines: readonly CodeLine[];
}

/** Template tag: `code\`${muted("import")} spanlight\`` → segments. */
function code(strings: TemplateStringsArray, ...segments: CodeSegment[]): CodeLine {
  const line: CodeSegment[] = [];
  strings.forEach((text, index) => {
    if (text) {
      line.push({ text, tone: "plain" });
    }
    const segment = segments[index];
    if (segment) {
      line.push(segment);
    }
  });
  return line;
}

function muted(text: string): CodeSegment {
  return { text, tone: "muted" };
}

function accent(text: string): CodeSegment {
  return { text, tone: "accent" };
}

const BLANK: CodeLine = [];

export const API_KEY_PLACEHOLDER = "<YOUR_API_KEY>";

const PYTHON_LINES: readonly CodeLine[] = [
  code`import spanlight`,
  code`from anthropic import Anthropic`,
  BLANK,
  code`${accent("spanlight.init")}(api_key="spl_live_…", host="http://localhost:8080")`,
  code`client = ${accent("spanlight.wrap_anthropic")}(Anthropic())`,
  BLANK,
  code`${accent("@spanlight.observe()")}`,
  code`def answer(question: str) -> str:`,
  code`    reply = client.messages.create(`,
  code`        model="claude-haiku-5-5",`,
  code`        max_tokens=300,`,
  code`        messages=[{"role": "user", "content": question}],`,
  code`    )`,
  code`    return reply.content[0].text`,
];

const OTLP_LINES: readonly CodeLine[] = [
  code`${muted("# Any OpenTelemetry SDK, in any language")}`,
  code`${muted("export")} OTEL_EXPORTER_OTLP_TRACES_ENDPOINT=${accent('"http://localhost:8080/v1/otlp/traces"')}`,
  code`${muted("export")} OTEL_EXPORTER_OTLP_TRACES_HEADERS=${accent(`"Authorization=Bearer%20${API_KEY_PLACEHOLDER}"`)}`,
  code`${muted("export")} OTEL_EXPORTER_OTLP_TRACES_PROTOCOL=${accent('"http/protobuf"')}`,
  code`${muted("export")} OTEL_SERVICE_NAME=${accent('"support-copilot"')}`,
];

const GATEWAY_KEY_PLACEHOLDER = "<YOUR_GATEWAY_KEY>";

const GATEWAY_LINES: readonly CodeLine[] = [
  code`${muted("# No SDK: point your existing client at the gateway")}`,
  code`${muted("from")} openai ${muted("import")} OpenAI`,
  BLANK,
  code`client = ${accent("OpenAI")}(`,
  code`    base_url=${accent('"http://localhost:8080/gw/v1"')},`,
  code`    api_key=${accent(`"${GATEWAY_KEY_PLACEHOLDER}"`)},`,
  code`)`,
  BLANK,
  code`response = client.chat.completions.${accent("create")}(`,
  code`    model=${accent('"gpt-4.1-mini"')},`,
  code`    messages=[{${accent('"role"')}: ${accent('"user"')}, ${accent('"content"')}: ${accent('"Say hello"')}}],`,
  code`)`,
];

export const CODE_SAMPLES: Record<CodeSampleId, CodeSample> = {
  python: {
    id: "python",
    label: "Python SDK",
    summary:
      "Wrap your OpenAI or Anthropic client and decorate a function. The SDK never raises into your code.",
    description: "Python SDK with the Anthropic client",
    lines: PYTHON_LINES,
  },
  otlp: {
    id: "otlp",
    label: "OpenTelemetry",
    summary:
      "Point any OTLP/HTTP exporter at /v1/otlp/traces. The GenAI conventions, OpenInference, OpenLLMetry and the Vercel AI SDK are read as sent.",
    description: "OpenTelemetry exporter environment",
    lines: OTLP_LINES,
  },
  gateway: {
    id: "gateway",
    label: "Gateway",
    summary:
      "Set your client’s base URL and change nothing else. It is the only option that sits in the request path.",
    description: "OpenAI client pointed at the Spanlight gateway, no SDK",
    lines: GATEWAY_LINES,
  },
};

/** Tab order. The id is also the lowercase tab name on the code card. */
export const CODE_SAMPLE_IDS: readonly CodeSampleId[] = ["python", "otlp", "gateway"];

/** The install command shown in the hero and under the Python sample (the README one). */
export const INSTALL_COMMAND = 'pip install "spanlight[anthropic]"';

export function isCodeSampleId(value: string): value is CodeSampleId {
  return Object.hasOwn(CODE_SAMPLES, value);
}

/** Plain text of the given lines, as it should land on the clipboard. */
export function codeToText(lines: readonly CodeLine[]): string {
  return lines.map((line) => line.map((segment) => segment.text).join("")).join("\n");
}
