import type { GatewayMode } from "@/lib/api";

/**
 * The URL apps send their provider calls to: this instance's own address plus `/gw/v1`. It is read
 * from the browser's location, so it is right behind any proxy.
 */
export function gatewayBaseUrl(origin: string = window.location.origin): string {
  return `${origin}/gw/v1`;
}

/** The Anthropic SDK adds `/v1` itself, so it gets the gateway's address without it. */
export function gatewayAnthropicBaseUrl(origin: string = window.location.origin): string {
  return `${origin}/gw`;
}

/** Stands in for the host of a gateway this instance doesn't serve (standalone or disabled). */
export const GATEWAY_HOST_PLACEHOLDER = "<your gateway URL>";

/** Why this origin is not the gateway's address, by mode, as a sentence for running text. */
export const GATEWAY_MODE_NOTES: Record<Exclude<GatewayMode, "embedded">, string> = {
  standalone: "This server runs the gateway standalone; use your gateway's own URL.",
  disabled: "The gateway is off on this server; set GATEWAY_MODE to turn it on.",
};

/** The host snippets point at: this origin when it serves the gateway, else the placeholder. */
export function gatewaySnippetHost(
  mode: GatewayMode,
  origin: string = window.location.origin,
): string {
  return mode === "embedded" ? origin : GATEWAY_HOST_PLACEHOLDER;
}
