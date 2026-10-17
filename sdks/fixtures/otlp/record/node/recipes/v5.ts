/**
 * `ai` 5: the global tracer provider plus `experimental_telemetry` on each call (spec §8.1,
 * `vercel-ai-legacy`). The AI SDK writes its `ai.*` attributes natively.
 */
import { createProvider, flush, scenario } from "./_otel.ts";
import { EXPERIMENTAL_TELEMETRY, runScenario } from "./_scenarios.ts";

const provider = createProvider();
await runScenario(scenario, EXPERIMENTAL_TELEMETRY);
await flush(provider);
