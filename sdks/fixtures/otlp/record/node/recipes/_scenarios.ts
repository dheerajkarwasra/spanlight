/**
 * The five Vercel AI SDK scenarios every Node recipe records (spec §8.1), against the fake
 * provider's Chat Completions route with the key `sk-fixture`.
 *
 * The call code is written once for `ai` 5, 6 and 7: what differs per major is only how
 * telemetry is switched on and how metadata is attached, which each recipe passes in as extra
 * call options. Retries are off so an error scenario makes exactly one provider call.
 */
import { createOpenAI } from "@ai-sdk/openai";
import { generateObject, generateText, streamText, tool } from "ai";
import { z } from "zod";

/** Extra call options per scenario: `metadata` for `generate_text_with_metadata`, else `plain`. */
export type TelemetryOptions = { plain: object; metadata: object };

/** `ai` 5 and 6: telemetry and metadata are per-call `experimental_telemetry`. */
export const EXPERIMENTAL_TELEMETRY: TelemetryOptions = {
  plain: { experimental_telemetry: { isEnabled: true } },
  metadata: {
    experimental_telemetry: { isEnabled: true, functionId: "answer", metadata: { tenant: "acme" } },
  },
};

/** `ai` 7: telemetry is on once registered; metadata travels as runtime context. */
export const REGISTERED_TELEMETRY: TelemetryOptions = {
  plain: {},
  metadata: {
    telemetry: { functionId: "answer", includeRuntimeContext: { tenant: true } },
    runtimeContext: { tenant: "acme" },
  },
};

// The fake-provider body each scenario is served (record/responses/openai/<body>.json or .sse).
const BODIES: Record<string, string> = {
  generate_text_with_metadata: "chat",
  stream_text: "chat_stream",
  tool_call: "chat_tools",
  generate_object: "chat",
  error: "error_429",
};
const HELLO = "Say hello in five words.";

export async function runScenario(name: string, telemetry: TelemetryOptions): Promise<void> {
  const body = BODIES[name];
  if (body === undefined) {
    throw new Error(`unknown scenario ${name}`);
  }
  const openai = createOpenAI({ baseURL: process.env.OPENAI_BASE_URL, apiKey: "sk-fixture" });
  const base = {
    model: openai.chat("gpt-4.1-mini"),
    maxRetries: 0,
    headers: { "x-fixture-scenario": body },
    ...(name === "generate_text_with_metadata" ? telemetry.metadata : telemetry.plain),
  };
  switch (name) {
    case "stream_text": {
      const result = streamText({ ...base, prompt: HELLO });
      for await (const _chunk of result.textStream) {
        // Drain the stream: the spans end when it does.
      }
      return;
    }
    case "tool_call":
      await generateText({
        ...base,
        prompt: "What is the weather in Paris and Tokyo? Use the tool.",
        tools: { get_weather: WEATHER },
      });
      return;
    case "generate_object":
      // The fake provider has no JSON-object body yet (owner action, record/README.md), so the
      // text answer fails schema parsing: the scenario records the AI SDK's failure path.
      await expectFailure(generateObject({ ...base, prompt: HELLO, schema: GREETING }));
      return;
    case "error":
      await expectFailure(generateText({ ...base, prompt: HELLO }));
      return;
    default:
      await generateText({ ...base, prompt: HELLO });
  }
}

const WEATHER = tool({
  description: "Get the current weather for a city.",
  inputSchema: z.object({ city: z.string(), unit: z.string() }),
  execute: async ({ city, unit }) => ({ city, unit, forecast: "not available in fixtures" }),
});
const GREETING = z.object({ greeting: z.string() });

async function expectFailure(call: Promise<unknown>): Promise<void> {
  try {
    await call;
  } catch {
    return;
  }
  throw new Error("the scenario was expected to fail");
}
