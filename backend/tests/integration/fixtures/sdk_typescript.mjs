// Exercise the built public package; credentials arrive only on stdin.
import { readFileSync } from "node:fs";
import { pathToFileURL } from "node:url";
const options = JSON.parse(readFileSync(0, "utf8"));
const { init, withSpan } = await import(pathToFileURL(options.sdk_path).href);
const client = init({
  apiKey: options.api_key,
  host: options.host,
  gzip: false,
  flushIntervalMs: 60000,
  maxRetries: 1,
});
let traceId;
withSpan(
  "network-root",
  (root) => {
    traceId = root.traceId;
    withSpan(
      "network-first",
      (first) => {
        first.setModel("gpt-4.1-mini", "openai");
        first.setUsage({
          inputTokens: 250000,
          outputTokens: 0,
          cachedTokens: 0,
        });
      },
      { kind: "llm" },
    );
    withSpan(
      "network-child",
      (child) => {
        child.setModel("gpt-4.1-mini", "openai");
        child.setUsage({
          inputTokens: 250000,
          outputTokens: 0,
          cachedTokens: 0,
        });
      },
      { kind: "llm" },
    );
  },
  { kind: "chain" },
);
if (!(await client.flush(15000))) throw new Error("flush timed out");
console.log(
  JSON.stringify({
    trace_id: traceId,
    sent: client.exporter.sentCount,
    failed: client.exporter.failedCount,
    dropped: client.exporter.droppedCount,
  }),
);
await client.shutdown();
