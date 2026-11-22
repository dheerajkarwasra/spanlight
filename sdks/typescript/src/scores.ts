/**
 * Building the `POST /v1/scores` payload for one score.
 * Mirrors `sdks/python/src/spanlight/_scores.py`.
 */

import { randomBytes } from "node:crypto";

import { log } from "./log.js";
import type { Payload } from "./span.js";

export type ScoreValue = number | boolean | string;

export interface ScoreOptions {
  spanId?: string;
  comment?: string;
}

/** Return a new RFC 9562 UUIDv7 string (48-bit Unix ms + 74 random bits). */
export function newUuid7(): string {
  const bytes = randomBytes(16);
  const millis = BigInt(Date.now());
  bytes[0] = Number((millis >> 40n) & 0xffn);
  bytes[1] = Number((millis >> 32n) & 0xffn);
  bytes[2] = Number((millis >> 24n) & 0xffn);
  bytes[3] = Number((millis >> 16n) & 0xffn);
  bytes[4] = Number((millis >> 8n) & 0xffn);
  bytes[5] = Number(millis & 0xffn);
  bytes[6] = ((bytes[6] ?? 0) & 0x0f) | 0x70;
  bytes[8] = ((bytes[8] ?? 0) & 0x3f) | 0x80;
  const hex = bytes.toString("hex");
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20, 32)}`;
}

/** Return the wire payload for one score, or undefined when invalid (logged as warning). */
export function buildScore(
  traceId: string,
  name: string,
  value: ScoreValue,
  options: ScoreOptions = {},
): Payload | undefined {
  if (typeof traceId !== "string" || typeof name !== "string" || !traceId || !name) {
    log.warn("Spanlight: score() needs non-empty traceId and name; score dropped");
    return undefined;
  }
  const payload: Payload = { id: newUuid7(), trace_id: traceId, name };
  if (typeof value === "boolean") {
    payload.value = value;
  } else if (typeof value === "number") {
    if (!Number.isFinite(value)) {
      log.warn(`Spanlight: score ${name} has a non-finite value; score dropped`);
      return undefined;
    }
    payload.value = value;
  } else if (typeof value === "string") {
    payload.string_value = value;
  } else {
    log.warn(`Spanlight: score ${name} has an unsupported value type; score dropped`);
    return undefined;
  }
  if (options.spanId !== undefined) payload.span_id = options.spanId;
  if (options.comment !== undefined) payload.comment = options.comment;
  return payload;
}
