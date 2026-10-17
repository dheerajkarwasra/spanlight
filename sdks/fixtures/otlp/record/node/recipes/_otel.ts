/**
 * Tracer setup shared by the Node recipes (spec §8.2).
 *
 * Importing this module makes the recording reproducible before any span exists: `Math.random`
 * (the source of the OTel SDK's trace and span ids) is replaced by a generator seeded with the
 * scenario name, and `performance.now` (span times and the AI SDK's `msToFirstChunk`) advances
 * one millisecond per call. A re-record of the same scenario then yields the same ids and the
 * same time to first token, so `record.py --check` can diff `expected` field by field. Import it
 * first: ES modules evaluate in import order.
 *
 * Export is OTLP/HTTP to the capture server named by `OTEL_EXPORTER_OTLP_TRACES_ENDPOINT`;
 * `flush` drains the batch before the recipe process exits (exactly one export per scenario).
 */
import { OTLPTraceExporter } from "@opentelemetry/exporter-trace-otlp-http";
import { resourceFromAttributes } from "@opentelemetry/resources";
import { BatchSpanProcessor, NodeTracerProvider } from "@opentelemetry/sdk-trace-node";

export const scenario = process.env.X_FIXTURE_SCENARIO ?? "";
if (!scenario) {
  throw new Error("X_FIXTURE_SCENARIO is not set: run the recipes through record.mjs");
}

/** mulberry32 seeded with the FNV-1a hash of `seed`: reproducible, not secure. */
export function seededRandom(seed: string): () => number {
  let state = 0x811c9dc5;
  for (const char of seed) {
    state = Math.imul(state ^ char.charCodeAt(0), 0x01000193) >>> 0;
  }
  return () => {
    state = (state + 0x6d2b79f5) >>> 0;
    let t = state;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 2 ** 32;
  };
}

Math.random = seededRandom(scenario);
let tick = 0;
Object.defineProperty(performance, "now", { value: () => ++tick, configurable: true });

/** The provider the AI SDK 5, 6 and 7-legacy recipes register globally. */
export function createProvider(): NodeTracerProvider {
  const provider = new NodeTracerProvider({
    resource: resourceFromAttributes({ "service.name": "spanlight-fixture" }),
    spanProcessors: [new BatchSpanProcessor(new OTLPTraceExporter())],
  });
  provider.register();
  return provider;
}

export async function flush(provider: {
  forceFlush(): Promise<void>;
  shutdown(): Promise<void>;
}): Promise<void> {
  await provider.forceFlush();
  await provider.shutdown();
}
