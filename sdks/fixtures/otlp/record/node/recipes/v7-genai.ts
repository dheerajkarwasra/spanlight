/**
 * `ai` 7 in GenAI mode, recorded through the documented recipe itself (`recipe.ts`, spec §9.1),
 * so the `vercel-ai-genai` fixture and the published recipe share one code path.
 */
import { flush, scenario } from "./_otel.ts";
import { trace, type ProxyTracerProvider } from "@opentelemetry/api";
import type { NodeTracerProvider } from "@opentelemetry/sdk-trace-node";
import "./recipe.ts";
import { REGISTERED_TELEMETRY, runScenario } from "./_scenarios.ts";

await runScenario(scenario, REGISTERED_TELEMETRY);
// The recipe registers its provider globally and exports nothing; flush it through the API.
const proxy = trace.getTracerProvider() as ProxyTracerProvider;
await flush(proxy.getDelegate() as NodeTracerProvider);
