import { PROVIDER_LABELS } from "@/features/gateway/credentials/credential-form";
import { baseUrlLabel } from "@/features/gateway/credentials/credential-model";
import type { Credential, Price, ProviderKind, ReplaySurface } from "@/lib/api";

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
  const priceProvider = provider === "gemini" ? "google" : provider;
  return [
    ...new Set(
      prices.filter((price) => price.provider === priceProvider).map((p) => p.model_pattern),
    ),
  ];
}

/** Credentials that can send a bounded replay on the recorded surface. */
export function replayCredentials(
  credentials: readonly Credential[],
  surface: ReplaySurface,
): Credential[] {
  return credentials.filter(({ provider }) => {
    if (surface === "messages") return provider === "anthropic";
    if (surface === "generate_content") return provider === "gemini";
    if (surface === "responses") {
      return provider === "openai" || provider === "openai_compatible" || provider === "xai";
    }
    return surface === "chat_completions" && provider !== "anthropic" && provider !== "gemini";
  });
}

/** Where a request through the credential goes: its base URL, or the provider's default host. */
export function credentialBaseUrl(credential: Credential): string {
  return baseUrlLabel(credential) ?? "Base URL unknown";
}
