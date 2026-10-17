/**
 * `ai` 7 in legacy mode: `registerTelemetry(new LegacyOpenTelemetry(...))` from `@ai-sdk/otel`
 * keeps the `ai.*` attributes of `ai` 5 and 6 (spec §8.1, `vercel-ai-legacy`).
 */
import { createProvider, flush, scenario } from "./_otel.ts";
import { LegacyOpenTelemetry } from "@ai-sdk/otel";
import { registerTelemetry } from "ai";
import { REGISTERED_TELEMETRY, runScenario } from "./_scenarios.ts";

const provider = createProvider();
registerTelemetry(new LegacyOpenTelemetry({ tracer: provider.getTracer("spanlight") }));
await runScenario(scenario, REGISTERED_TELEMETRY);
await flush(provider);
