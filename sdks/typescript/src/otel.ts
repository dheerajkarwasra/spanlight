/**
 * OpenTelemetry, Vercel AI SDK (`experimental_telemetry`) and MCP propagation helpers.
 */

import { getClient } from "./client.js";
import { cleanMetadata } from "./metadata.js";
import { continueTrace, inject } from "./propagation.js";
import { withSpan } from "./client.js";

export interface VercelAiTelemetryOptions {
  functionId?: string;
  metadata?: Record<string, unknown>;
  recordInputs?: boolean;
  recordOutputs?: boolean;
  isEnabled?: boolean;
}

export interface VercelAiTelemetry {
  isEnabled: boolean;
  functionId?: string;
  metadata?: Record<string, string>;
  recordInputs: boolean;
  recordOutputs: boolean;
}

export interface OtlpConfigOptions {
  host?: string;
  apiKey?: string;
}

export interface OtlpTraceConfig {
  url: string;
  headers: Record<string, string>;
}

/** Build a Vercel AI SDK `experimental_telemetry` settings object wired to Spanlight's metadata rules. */
export function vercelAiTelemetry(options: VercelAiTelemetryOptions = {}): VercelAiTelemetry {
  const client = getClient();
  const metadata = options.metadata !== undefined ? cleanMetadata(options.metadata) : undefined;
  const result: VercelAiTelemetry = {
    isEnabled: options.isEnabled ?? client.enabled,
    recordInputs: options.recordInputs ?? true,
    recordOutputs: options.recordOutputs ?? true,
  };
  if (options.functionId !== undefined) result.functionId = options.functionId;
  if (metadata !== undefined && Object.keys(metadata).length > 0) result.metadata = metadata;
  return result;
}

/** Return `{ url, headers }` for forwarding OpenTelemetry JS OTLP/HTTP exporters to Spanlight. */
export function otlpTraceConfig(options: OtlpConfigOptions = {}): OtlpTraceConfig {
  const env = process.env;
  let host = options.host ?? (env.SPANLIGHT_HOST || "http://localhost:8000");
  while (host.endsWith("/")) host = host.slice(0, -1);
  const apiKey = options.apiKey ?? (env.SPANLIGHT_API_KEY || "");
  const headers: Record<string, string> = {};
  if (apiKey) headers.Authorization = `Bearer ${apiKey}`;
  return { url: `${host}/v1/otlp/traces`, headers };
}

/** Inject current trace context into MCP request `params._meta` inside an active `tool` span. */
export function withMcpToolCall<TParams extends Record<string, unknown>, TResult>(
  toolName: string,
  params: TParams,
  fn: (paramsWithMeta: TParams & { _meta: Record<string, unknown> }) => TResult,
): TResult {
  return withSpan(
    toolName,
    () => {
      const existingMeta = params._meta as Record<string, unknown> | undefined;
      const nextParams = { ...params, _meta: inject(existingMeta) };
      return fn(nextParams);
    },
    { kind: "tool", input: params, attributes: { "mcp.tool.name": toolName } },
  );
}

/** Continue an incoming W3C trace from MCP `request.params._meta` inside a child `tool` span. */
export function traceMcpServerTool<T>(
  toolName: string,
  params: { _meta?: Record<string, unknown>; meta?: Record<string, unknown> } | undefined,
  fn: () => T,
): T {
  const carrier = params?._meta ?? params?.meta;
  return continueTrace(carrier, () =>
    withSpan(toolName, fn, {
      kind: "tool",
      input: params,
      attributes: { "mcp.tool.name": toolName, "rpc.system": "mcp" },
    }),
  );
}
