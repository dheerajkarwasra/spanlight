/** OpenTelemetry semantic-convention namespaces that describe the HTTP call, not the work. */
const TRANSPORT_PREFIXES = ["http.", "net.", "url.", "server.", "network.", "user_agent."];

/** A span's attributes split into its own keys and the transport keys shown collapsed. */
export function splitTransportAttributes(attributes: Record<string, unknown>): {
  main: [string, unknown][];
  transport: [string, unknown][];
} {
  const main: [string, unknown][] = [];
  const transport: [string, unknown][] = [];
  for (const entry of Object.entries(attributes)) {
    const isTransport = TRANSPORT_PREFIXES.some((prefix) => entry[0].startsWith(prefix));
    (isTransport ? transport : main).push(entry);
  }
  return { main, transport };
}
