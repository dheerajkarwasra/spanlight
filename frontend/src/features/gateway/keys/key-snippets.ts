import type { GatewayMode } from "@/lib/api";

import { gatewayAnthropicBaseUrl, gatewayBaseUrl, gatewaySnippetHost } from "../gateway-url";

export type SdkChoice = "openai" | "anthropic";

/**
 * The lines that point an SDK at the gateway with the key in the environment, not in code. A
 * gateway this origin doesn't serve gets a placeholder host, never this origin.
 */
export function sdkSnippet(sdk: SdkChoice, mode: GatewayMode): string {
  const host = gatewaySnippetHost(mode);
  if (sdk === "openai") {
    return [
      "client = OpenAI(",
      `    base_url="${gatewayBaseUrl(host)}",`,
      '    api_key=os.environ["SPANLIGHT_GATEWAY_KEY"],',
      ")",
    ].join("\n");
  }
  return [
    "client = Anthropic(",
    `    base_url="${gatewayAnthropicBaseUrl(host)}",`,
    '    api_key=os.environ["SPANLIGHT_GATEWAY_KEY"],',
    ")",
  ].join("\n");
}
