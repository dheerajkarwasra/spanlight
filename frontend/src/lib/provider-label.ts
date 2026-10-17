/**
 * The one set of display names for LLM provider slugs, used by Overview, Provider health and the
 * incident overlays, so a provider reads the same on every page. Lookup ignores case (span
 * providers keep their sender's case); a slug not listed here is shown as sent.
 */
const PROVIDER_LABELS: Record<string, string> = {
  anthropic: "Anthropic",
  openai: "OpenAI",
  openai_compatible: "OpenAI-compatible",
  azure: "Azure",
  azure_openai: "Azure OpenAI",
  google: "Google",
  gemini: "Google Gemini",
  vertex_ai: "Google Vertex AI",
  bedrock: "Amazon Bedrock",
  aws_bedrock: "Amazon Bedrock",
  meta: "Meta",
  mistral: "Mistral",
  cohere: "Cohere",
  groq: "Groq",
  ollama: "Ollama",
  together: "Together",
  deepseek: "DeepSeek",
  xai: "xAI",
};

/** A provider's display name; "Unknown provider" for a call that named none. */
export function providerLabel(provider: string | null): string {
  if (provider === null || provider.trim() === "") {
    return "Unknown provider";
  }
  return PROVIDER_LABELS[provider.toLowerCase()] ?? provider;
}

/** A status feed's name: Google's feed is the Google Cloud status page. */
export function feedLabel(provider: string): string {
  return provider.toLowerCase() === "google" ? "Google Cloud" : providerLabel(provider);
}
