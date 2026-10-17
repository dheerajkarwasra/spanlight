import {
  isApiError,
  type BillingProvider,
  type BillingSource,
  type BillingSourceCreate,
  type BillingSourceKind,
  type BillingSourceUpdate,
} from "@/lib/api";

export const SOURCE_NAME_MAX_LENGTH = 128;
export const ADMIN_KEY_MAX_LENGTH = 512;
export const PROVIDER_SCOPE_MAX_LENGTH = 128;

/** The kinds that fetch from a provider with an admin key (spec §8.2). */
export type ApiSourceKind = Exclude<BillingSourceKind, "csv_upload">;

/** The provider an API kind fetches from; a CSV source names its own. */
export const PROVIDER_OF_KIND: Record<ApiSourceKind, BillingProvider> = {
  anthropic_admin_api: "anthropic",
  openai_costs_api: "openai",
};

const ADMIN_KEY_RULES: Record<ApiSourceKind, { prefix: string; message: string }> = {
  anthropic_admin_api: {
    prefix: "sk-ant-admin",
    message: "An Anthropic Admin API key starts with sk-ant-admin.",
  },
  openai_costs_api: { prefix: "sk-admin", message: "An OpenAI admin key starts with sk-admin." },
};

export function isApiKind(kind: BillingSourceKind): kind is ApiSourceKind {
  return kind !== "csv_upload";
}

/** The key prefix the API kind's hint names. */
export function adminKeyPrefix(kind: ApiSourceKind): string {
  return ADMIN_KEY_RULES[kind].prefix;
}

/** The admin key field's hint for an API kind. */
export function adminKeyHint(kind: ApiSourceKind): string {
  return `Starts with ${adminKeyPrefix(kind)}. Checked once with a one-day fetch, stored encrypted, never shown again.`;
}

/** `409 NOT_CONFIGURED`: the server has no CREDENTIALS_KEYS, so no admin key can be stored. */
export function isNotConfigured(error: unknown): boolean {
  return isApiError(error) && error.status === 409 && error.code === "NOT_CONFIGURED";
}

/** The notice when the server sent no detail of its own with its `409 NOT_CONFIGURED`. */
export const NOT_CONFIGURED_FALLBACK =
  "Billing sources that fetch from a provider are encrypted with a server key that isn’t set. Ask your administrator to set CREDENTIALS_KEYS, or add a CSV source.";

/** The notice of a `409 NOT_CONFIGURED`: the server's own `detail`, so the two never drift. */
export function notConfiguredCopy(error: unknown): string {
  const detail = isApiError(error) ? error.detail?.trim() : undefined;
  return detail ? detail : NOT_CONFIGURED_FALLBACK;
}

/** The create and edit dialog's values. `adminKey` is only ever what was typed, never stored. */
export interface SourceFormValues {
  kind: BillingSourceKind;
  name: string;
  /** A CSV source's provider; "" until one is chosen. Ignored for the API kinds. */
  provider: BillingProvider | "";
  adminKey: string;
  providerScope: string;
  projectIds: string[];
  enabled: boolean;
}

export type SourceField =
  "kind" | "name" | "provider" | "adminKey" | "providerScope" | "projectIds";
export type FieldErrors = Partial<Record<SourceField, string>>;
export type SourceFormMode = "create" | "edit";

/** The API's body field names, mapped to the form field that shows their 422 or 409 messages. */
export const SOURCE_FIELD_OF_API_FIELD: Record<string, SourceField> = {
  kind: "kind",
  name: "name",
  provider: "provider",
  admin_key: "adminKey",
  provider_scope: "providerScope",
  project_ids: "projectIds",
};

export function emptySourceForm(): SourceFormValues {
  return {
    kind: "anthropic_admin_api",
    name: "",
    provider: "",
    adminKey: "",
    providerScope: "",
    projectIds: [],
    enabled: true,
  };
}

/** The edit dialog's starting values: the stored source, with an empty key field. */
export function sourceFormOf(source: BillingSource): SourceFormValues {
  return {
    kind: source.kind,
    name: source.name,
    provider: source.provider,
    adminKey: "",
    providerScope: source.provider_scope ?? "",
    projectIds: source.projects.map((project) => project.id),
    enabled: source.enabled,
  };
}

/** The provider the form's source reconciles, or null while a CSV source has none chosen. */
export function providerOfForm(values: SourceFormValues): BillingProvider | null {
  if (isApiKind(values.kind)) {
    return PROVIDER_OF_KIND[values.kind];
  }
  return values.provider === "" ? null : values.provider;
}

/** Why a typed admin key cannot be sent for `kind`, or undefined when it can. */
export function adminKeyError(kind: ApiSourceKind, raw: string): string | undefined {
  const key = raw.trim();
  if (key === "") {
    return "Paste the Admin API key.";
  }
  if (key.length > ADMIN_KEY_MAX_LENGTH) {
    return `Use ${String(ADMIN_KEY_MAX_LENGTH)} characters or fewer.`;
  }
  if (!/^[!-~]+$/.test(key)) {
    return "Use only visible characters, without spaces.";
  }
  const rule = ADMIN_KEY_RULES[kind];
  return key.startsWith(rule.prefix) ? undefined : rule.message;
}

/**
 * The client-side checks of spec §8.2: a name of 1–128 characters, the kind's key prefix (create
 * only; a key is changed by rotating), a provider for a CSV source, a scope of at most 128
 * characters. The server checks all of them again.
 */
export function validateSourceForm(
  values: SourceFormValues,
  mode: SourceFormMode = "create",
): FieldErrors {
  const errors: FieldErrors = {};
  const name = values.name.trim();
  if (name === "") {
    errors.name = "Enter a name for this source.";
  } else if (name.length > SOURCE_NAME_MAX_LENGTH) {
    errors.name = `Use ${String(SOURCE_NAME_MAX_LENGTH)} characters or fewer.`;
  }
  if (isApiKind(values.kind)) {
    const keyError = mode === "create" ? adminKeyError(values.kind, values.adminKey) : undefined;
    if (keyError) {
      errors.adminKey = keyError;
    }
  } else if (values.provider === "") {
    errors.provider = "Choose the provider whose costs the file holds.";
  }
  if (values.providerScope.trim().length > PROVIDER_SCOPE_MAX_LENGTH) {
    errors.providerScope = `Use ${String(PROVIDER_SCOPE_MAX_LENGTH)} characters or fewer.`;
  }
  return errors;
}

/** The create body: the key and scope for an API kind, the provider for a CSV source. */
export function toSourceCreate(values: SourceFormValues): BillingSourceCreate {
  const body: BillingSourceCreate = {
    kind: values.kind,
    name: values.name.trim(),
    project_ids: values.projectIds,
  };
  if (isApiKind(values.kind)) {
    body.admin_key = values.adminKey.trim();
    const scope = values.providerScope.trim();
    if (scope !== "") {
      body.provider_scope = scope;
    }
  } else if (values.provider !== "") {
    body.provider = values.provider;
  }
  return body;
}

/** The edit body: the fields that changed, and always the whole project list (a replacement). */
export function toSourceUpdate(
  values: SourceFormValues,
  source: BillingSource,
): BillingSourceUpdate {
  const body: BillingSourceUpdate = {};
  const name = values.name.trim();
  if (name !== source.name) {
    body.name = name;
  }
  const scope = values.providerScope.trim() || null;
  if (isApiKind(source.kind) && scope !== source.provider_scope) {
    body.provider_scope = scope;
  }
  if (values.enabled !== source.enabled) {
    body.enabled = values.enabled;
  }
  body.project_ids = values.projectIds;
  return body;
}

/**
 * Project id to the name of the other source of `provider` that already reconciles it: a project
 * is compared with one account per provider, so the dialog offers those disabled.
 */
export function takenProjects(
  sources: readonly BillingSource[],
  provider: BillingProvider | null,
  exceptSourceId: string | null,
): Map<string, string> {
  const taken = new Map<string, string>();
  if (provider === null) {
    return taken;
  }
  for (const source of sources) {
    if (source.provider !== provider || source.id === exceptSourceId) {
      continue;
    }
    for (const project of source.projects) {
      taken.set(project.id, source.name);
    }
  }
  return taken;
}

/**
 * The form fields a refused create or edit points at: a taken name, a project another source of
 * the provider covers (the detail names that source), or a 422's fields. Null when no field
 * explains the error, so the caller shows it as a toast.
 */
export function sourceErrorsOf(error: unknown): FieldErrors | null {
  if (!isApiError(error)) {
    return null;
  }
  if (error.code === "BILLING_SOURCE_NAME_TAKEN") {
    return { name: error.message };
  }
  if (error.code === "BILLING_SOURCE_PROJECT_TAKEN") {
    return { projectIds: error.message };
  }
  const errors: FieldErrors = {};
  for (const fieldError of error.fieldErrors) {
    const field = SOURCE_FIELD_OF_API_FIELD[fieldError.field];
    if (field) {
      errors[field] = fieldError.message;
    }
  }
  return Object.keys(errors).length > 0 ? errors : null;
}
