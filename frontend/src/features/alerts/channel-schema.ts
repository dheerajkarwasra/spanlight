import { z } from "zod";

import type {
  AlertChannel,
  AlertChannelCreate,
  AlertChannelKind,
  AlertChannelUpdate,
  PagerDutySeverity,
} from "@/lib/api";

/*
 * The channel dialog's form: one set of values for every kind, validated per kind. The bounds
 * mirror the server's (`alerts/schemas.py`): names of 1 to 80 characters, 1 to 10 recipients, a
 * Slack URL under `https://hooks.slack.com/`, an `https://` webhook URL and a 32-character
 * PagerDuty routing key. The server stays authoritative (members only, private addresses).
 */

export const CHANNEL_NAME_MAX_LENGTH = 80;
export const MAX_RECIPIENTS = 10;
export const URL_MAX_LENGTH = 2048;
export const SLACK_URL_PREFIX = "https://hooks.slack.com/";
export const ROUTING_KEY_LENGTH = 32;

export const CHANNEL_KINDS: readonly AlertChannelKind[] = [
  "email",
  "slack",
  "webhook",
  "pagerduty",
];

export const SEVERITIES: readonly PagerDutySeverity[] = ["critical", "error", "warning", "info"];

export const SEVERITY_LABELS: Record<PagerDutySeverity, string> = {
  critical: "Critical",
  error: "Error",
  warning: "Warning",
  info: "Info",
};

export interface ChannelFormValues {
  kind: AlertChannelKind;
  name: string;
  /** Email channels. */
  recipients: string[];
  /** Slack: the incoming-webhook URL, a secret. Blank on edit keeps the stored one. */
  slackUrl: string;
  /** Webhook: where the signed POST goes. Not a secret. */
  webhookUrl: string;
  /** PagerDuty: the Events API v2 routing key, a secret. Blank on edit keeps the stored one. */
  routingKey: string;
  severity: PagerDutySeverity;
}

/** Create needs every secret; edit takes a blank secret as "keep the stored one". */
export type ChannelFormMode = "create" | "edit";

/**
 * Whether the text is an `http(s)://` URL with a host and no user info. Plain `http://` passes
 * because a self-hoster may allow private targets; the server's `UNSAFE_URL` decides the rest.
 */
export function isWebUrl(value: string): boolean {
  try {
    const url = new URL(value);
    return (
      (url.protocol === "https:" || url.protocol === "http:") &&
      url.hostname !== "" &&
      url.username === ""
    );
  } catch {
    return false;
  }
}

/** Whether the text is a Slack incoming-webhook URL: the prefix, then a path, no spaces. */
export function isSlackUrl(value: string): boolean {
  return (
    value.startsWith(SLACK_URL_PREFIX) &&
    value.length > SLACK_URL_PREFIX.length &&
    /^[!-~]+$/.test(value)
  );
}

/**
 * zod's default address pattern with letters, digits and marks from any script, so an
 * internationalized address the API accepts (`ops@例え.jp`) can be added; the domain still needs
 * a dot and a two-letter top level. The API stays the authority.
 */
const UNICODE_EMAIL =
  /^(?:[\p{L}\p{M}\p{N}_'+-]+\.)*[\p{L}\p{M}\p{N}_'+-]*[\p{L}\p{M}\p{N}_+-]@(?:[\p{L}\p{M}\p{N}][\p{L}\p{M}\p{N}-]*\.)+\p{L}{2,}$/u;

const emailSchema = z.email({ pattern: UNICODE_EMAIL });

/** The reason one address can't be a recipient, or null. Used by the picker before adding it. */
export function recipientProblem(address: string, current: readonly string[]): string | null {
  if (!emailSchema.safeParse(address).success) {
    return `${address} isn't an email address.`;
  }
  if (current.length >= MAX_RECIPIENTS) {
    return `An email channel can have at most ${MAX_RECIPIENTS} recipients.`;
  }
  return null;
}

type Issue = { path: keyof ChannelFormValues; message: string };

function kindIssues(values: ChannelFormValues, mode: ChannelFormMode): Issue[] {
  switch (values.kind) {
    case "email":
      if (values.recipients.length === 0) {
        return [{ path: "recipients", message: "Add at least one recipient." }];
      }
      if (values.recipients.length > MAX_RECIPIENTS) {
        return [{ path: "recipients", message: `Pick ${MAX_RECIPIENTS} recipients or fewer.` }];
      }
      return [];
    case "slack":
      if (values.slackUrl === "") {
        return mode === "edit" ? [] : [{ path: "slackUrl", message: "Paste the webhook URL." }];
      }
      return isSlackUrl(values.slackUrl)
        ? []
        : [{ path: "slackUrl", message: `Use a Slack URL that starts with ${SLACK_URL_PREFIX}` }];
    case "webhook":
      if (values.webhookUrl === "") {
        return [{ path: "webhookUrl", message: "Enter the endpoint URL." }];
      }
      return isWebUrl(values.webhookUrl)
        ? []
        : [{ path: "webhookUrl", message: "Use a full URL that starts with https://" }];
    case "pagerduty":
      if (values.routingKey === "") {
        return mode === "edit" ? [] : [{ path: "routingKey", message: "Paste the routing key." }];
      }
      return /^[A-Za-z0-9]{32}$/.test(values.routingKey)
        ? []
        : [
            {
              path: "routingKey",
              message: `Use the ${ROUTING_KEY_LENGTH}-character key, letters and digits only.`,
            },
          ];
  }
}

export function channelFormSchema(mode: ChannelFormMode) {
  return z
    .object({
      kind: z.enum(["email", "slack", "webhook", "pagerduty"]),
      name: z
        .string()
        .trim()
        .min(1, "Enter a name for this channel.")
        .max(CHANNEL_NAME_MAX_LENGTH, `Use ${CHANNEL_NAME_MAX_LENGTH} characters or fewer.`),
      recipients: z.array(z.string()),
      slackUrl: z.string().trim().max(URL_MAX_LENGTH, "This URL is too long."),
      webhookUrl: z.string().trim().max(URL_MAX_LENGTH, "This URL is too long."),
      routingKey: z.string().trim(),
      severity: z.enum(["critical", "error", "warning", "info"]),
    })
    .superRefine((values, context) => {
      for (const issue of kindIssues(values, mode)) {
        context.addIssue({ code: "custom", path: [issue.path], message: issue.message });
      }
    });
}

export const EMPTY_CHANNEL_VALUES: ChannelFormValues = {
  kind: "email",
  name: "",
  recipients: [],
  slackUrl: "",
  webhookUrl: "",
  routingKey: "",
  severity: "error",
};

/** The form's starting values for an edit. Secrets are never sent back, so their fields start blank. */
export function valuesFromChannel(channel: AlertChannel): ChannelFormValues {
  const config = channel.config;
  return {
    ...EMPTY_CHANNEL_VALUES,
    kind: channel.kind,
    name: channel.name,
    recipients: "to" in config ? [...config.to] : [],
    webhookUrl: "url" in config ? config.url : "",
    severity: "severity" in config ? config.severity : "error",
  };
}

/** The create body for the values' kind; fields of other kinds are left out. */
export function toChannelCreate(values: ChannelFormValues): AlertChannelCreate {
  switch (values.kind) {
    case "email":
      return { kind: "email", name: values.name, config: { to: values.recipients } };
    case "slack":
      return { kind: "slack", name: values.name, secret: values.slackUrl };
    case "webhook":
      return { kind: "webhook", name: values.name, config: { url: values.webhookUrl } };
    case "pagerduty":
      return {
        kind: "pagerduty",
        name: values.name,
        secret: values.routingKey,
        config: { severity: values.severity },
      };
  }
}

/** The edit body: only what changed. A blank secret field keeps the stored secret. */
export function toChannelUpdate(
  values: ChannelFormValues,
  channel: AlertChannel,
): AlertChannelUpdate {
  const initial = valuesFromChannel(channel);
  const update: AlertChannelUpdate = {};
  if (values.name !== initial.name) {
    update.name = values.name;
  }
  if (values.kind === "email" && values.recipients.join(",") !== initial.recipients.join(",")) {
    update.config = { to: values.recipients };
  }
  if (values.kind === "webhook" && values.webhookUrl !== initial.webhookUrl) {
    update.config = { url: values.webhookUrl };
  }
  if (values.kind === "pagerduty" && values.severity !== initial.severity) {
    update.config = { severity: values.severity };
  }
  const secret = values.kind === "slack" ? values.slackUrl : values.routingKey;
  if ((values.kind === "slack" || values.kind === "pagerduty") && secret !== "") {
    update.secret = secret;
  }
  return update;
}

/**
 * The form field a server field error belongs to (`config.to` → recipients, `secret` → the kind's
 * secret field). Null for a path the form has no field for.
 */
export function channelFieldOf(
  path: string,
  kind: AlertChannelKind,
): keyof ChannelFormValues | null {
  const root = path.split(".")[0];
  if (root === "name") {
    return "name";
  }
  if (root === "secret") {
    return kind === "slack" ? "slackUrl" : kind === "pagerduty" ? "routingKey" : null;
  }
  if (root !== "config") {
    return null;
  }
  const field = path.split(".")[1];
  if (field === "to") {
    return "recipients";
  }
  if (field === "url") {
    return "webhookUrl";
  }
  return field === "severity" ? "severity" : null;
}

/** Slack, webhook and PagerDuty channels keep a secret, so they need the server's key. */
export function needsCredentialsKey(kind: AlertChannelKind): boolean {
  return kind !== "email";
}
