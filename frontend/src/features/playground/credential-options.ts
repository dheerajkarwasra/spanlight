import type { Credential, Price, ProviderKind } from "@/lib/api";

const PROVIDER_LABELS: Record<ProviderKind, string> = {
  openai: "OpenAI",
  anthropic: "Anthropic",
  openai_compatible: "OpenAI-compatible",
};

/** "Anthropic · acme-anthropic", as the Figma credential select reads. */
export function credentialLabel(credential: Credential): string {
  return `${PROVIDER_LABELS[credential.provider]} · ${credential.name}`;
}

/** Models the price table knows for a provider: suggestions, never a limit. */
export function modelSuggestions(
  prices: readonly Price[],
  provider: ProviderKind | null,
): string[] {
  if (provider === null || provider === "openai_compatible") {
    return [];
  }
  return [
    ...new Set(prices.filter((price) => price.provider === provider).map((p) => p.model_pattern)),
  ];
}
