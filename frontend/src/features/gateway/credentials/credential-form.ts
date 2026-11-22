import { z } from "zod";

import type { CredentialCreate, ProviderKind } from "@/lib/api";

export const CREDENTIAL_NAME_MAX_LENGTH = 100;
export const API_KEY_MAX_LENGTH = 512;
export const BASE_URL_MAX_LENGTH = 2048;

export const API_KEY_HINT = "Stored encrypted. Never shown again.";
export const BASE_URL_HINT =
  "HTTPS only. Private and local addresses are blocked unless your administrator allows them.";

export const AZURE_URL_HINT = "Use the Azure OpenAI resource URL ending in /openai/v1.";
const PROVIDERS = [
  "openai",
  "anthropic",
  "gemini",
  "azure_openai",
  "mistral",
  "groq",
  "deepseek",
  "xai",
  "together",
  "openai_compatible",
] as const satisfies readonly ProviderKind[];

export const PROVIDER_LABELS: Record<ProviderKind, string> = {
  openai: "OpenAI",
  anthropic: "Anthropic",
  gemini: "Google Gemini",
  openai_compatible: "OpenAI-compatible",
  azure_openai: "Azure OpenAI",
  mistral: "Mistral",
  groq: "Groq",
  deepseek: "DeepSeek",
  xai: "xAI",
  together: "Together AI",
};

export const PROVIDER_HINTS: Partial<Record<ProviderKind, string>> = {
  anthropic: "Serves the Messages API. Point the Anthropic SDK at /gw.",
  gemini: "Supports native Gemini and OpenAI-compatible requests.",
  openai_compatible: "Any server that speaks the OpenAI Chat Completions API.",
};

export function needsBaseUrl(provider: ProviderKind): boolean {
  return provider === "openai_compatible" || provider === "azure_openai";
}

const apiKeySchema = z
  .string()
  .trim()
  .min(1, "Enter the API key.")
  .max(API_KEY_MAX_LENGTH, `Use ${API_KEY_MAX_LENGTH} characters or fewer.`)
  // Provider keys are visible ASCII; the server refuses anything else.
  .regex(/^[!-~]+$/, "Use only visible characters, without spaces.");

/** The API key field of the rotate dialog. */
export const rotateSchema = z.object({ apiKey: apiKeySchema });
export type RotateValues = z.infer<typeof rotateSchema>;

/** Whether the text is an `https://` URL. The server does the full check (hosts, addresses). */
export function isHttpsUrl(value: string): boolean {
  try {
    return new URL(value).protocol === "https:";
  } catch {
    return false;
  }
}

function isAzureResourceUrl(value: string): boolean {
  const url = new URL(value);
  return (
    /^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.openai\.azure\.com$/.test(url.hostname) &&
    /^\/openai\/v1\/?$/.test(url.pathname) &&
    !url.port &&
    !url.username &&
    !url.password &&
    !/[?#]/.test(value)
  );
}

export const credentialSchema = z
  .object({
    name: z
      .string()
      .trim()
      .min(1, "Enter a name for this credential.")
      .max(CREDENTIAL_NAME_MAX_LENGTH, `Use ${CREDENTIAL_NAME_MAX_LENGTH} characters or fewer.`),
    provider: z.enum(PROVIDERS),
    apiKey: apiKeySchema,
    baseUrl: z.string().trim().max(BASE_URL_MAX_LENGTH, "This URL is too long."),
  })
  .superRefine((values, context) => {
    if (!needsBaseUrl(values.provider)) {
      return;
    }
    if (values.baseUrl === "") {
      context.addIssue({
        code: "custom",
        path: ["baseUrl"],
        message: "Enter the server's base URL.",
      });
    } else if (!isHttpsUrl(values.baseUrl)) {
      context.addIssue({
        code: "custom",
        path: ["baseUrl"],
        message: "Use an https:// URL.",
      });
    } else if (values.provider === "azure_openai" && !isAzureResourceUrl(values.baseUrl)) {
      context.addIssue({ code: "custom", path: ["baseUrl"], message: AZURE_URL_HINT });
    }
  });

export type CredentialValues = z.infer<typeof credentialSchema>;

/** The API's body field names, mapped to the form field that shows their 422 messages. */
export const CREDENTIAL_FIELD_OF_API_FIELD = {
  name: "name",
  api_key: "apiKey",
  base_url: "baseUrl",
} as const satisfies Record<string, keyof CredentialValues>;

/** The request body for a form's values. `base_url` is sent only where it applies. */
export function toCredentialCreate(values: CredentialValues): CredentialCreate {
  const body: CredentialCreate = {
    name: values.name,
    provider: values.provider,
    api_key: values.apiKey,
  };
  if (needsBaseUrl(values.provider)) {
    body.base_url = values.baseUrl;
  }
  return body;
}
