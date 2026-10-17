import { baseUrlLabel } from "@/features/gateway/credentials/credential-model";
import type { Credential, Price, ProviderKind, ReplayProviderKind } from "@/lib/api";

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

/**
 * Credentials that can send a replay of a span needing `kind`: Anthropic takes Anthropic only;
 * OpenAI takes OpenAI and any OpenAI-compatible server (Azure, Groq, Ollama, …).
 */
export function replayCredentials(
  credentials: readonly Credential[],
  kind: ReplayProviderKind,
): Credential[] {
  return credentials.filter((credential) =>
    kind === "anthropic"
      ? credential.provider === "anthropic"
      : credential.provider === "openai" || credential.provider === "openai_compatible",
  );
}

/** Where a request through the credential goes: its base URL, or the provider's default host. */
export function credentialBaseUrl(credential: Credential): string {
  return baseUrlLabel(credential) ?? "Base URL unknown";
}
