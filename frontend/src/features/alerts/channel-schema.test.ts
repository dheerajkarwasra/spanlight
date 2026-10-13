import { describe, expect, it } from "vitest";

import type { AlertChannel } from "@/lib/api";

import {
  EMPTY_CHANNEL_VALUES,
  MAX_RECIPIENTS,
  channelFieldOf,
  channelFormSchema,
  isSlackUrl,
  isWebUrl,
  recipientProblem,
  toChannelCreate,
  toChannelUpdate,
  type ChannelFormValues,
} from "./channel-schema";

// The bounds are the server's (`app/alerts/schemas.py`): the form must not let through what the
// API rejects, and must not block what it accepts.

const ROUTING_KEY = "a".repeat(16) + "B".repeat(8) + "01234567";

function values(overrides: Partial<ChannelFormValues>): ChannelFormValues {
  return { ...EMPTY_CHANNEL_VALUES, name: "On-call", ...overrides };
}

function problems(mode: "create" | "edit", input: ChannelFormValues): Record<string, string> {
  const result = channelFormSchema(mode).safeParse(input);
  if (result.success) return {};
  return Object.fromEntries(
    result.error.issues.map((issue) => [issue.path.join("."), issue.message]),
  );
}

function channel(overrides: Partial<AlertChannel>): AlertChannel {
  return {
    id: "ch_1",
    org_id: "org_1",
    kind: "email",
    name: "On-call",
    config: { to: ["ops@acme.com"] },
    has_secret: false,
    verified_at: null,
    created_at: "2026-10-01T00:00:00Z",
    updated_at: "2026-10-01T00:00:00Z",
    ...overrides,
  };
}

describe("channel name", () => {
  it("rejects a blank or whitespace-only name", () => {
    expect(problems("create", values({ name: "   ", recipients: ["a@acme.com"] }))).toHaveProperty(
      "name",
    );
  });

  it("allows 80 characters after trimming and rejects 81", () => {
    const ok = values({ name: ` ${"n".repeat(80)} `, recipients: ["a@acme.com"] });
    expect(problems("create", ok)).toEqual({});
    const long = values({ name: "n".repeat(81), recipients: ["a@acme.com"] });
    expect(problems("create", long)).toHaveProperty("name");
  });
});

describe("email channels", () => {
  it("need 1 to 10 recipients", () => {
    expect(problems("create", values({ recipients: [] }))).toHaveProperty("recipients");
    const eleven = Array.from({ length: 11 }, (_, index) => `u${String(index)}@acme.com`);
    expect(problems("create", values({ recipients: eleven }))).toHaveProperty("recipients");
    expect(problems("create", values({ recipients: eleven.slice(0, 10) }))).toEqual({});
  });

  it("refuses to add an 11th recipient or a malformed address in the picker", () => {
    const ten = Array.from({ length: MAX_RECIPIENTS }, (_, index) => `u${String(index)}@acme.com`);
    expect(recipientProblem("new@acme.com", ten)).toMatch(/at most 10/);
    expect(recipientProblem("not-an-address", [])).toMatch(/isn't an email address/);
    expect(recipientProblem("ops@acme", [])).not.toBeNull();
    expect(recipientProblem("ops@acme.com", [])).toBeNull();
  });

  // Finding T8-02: the server (pydantic EmailStr) accepts internationalized domains such as
  // `ops@例え.jp`, but the picker refused them, so such a recipient could never be added.
  it("accepts an internationalized address the server accepts", () => {
    expect(recipientProblem("ops@例え.jp", [])).toBeNull();
    expect(recipientProblem("δοκιμή@παράδειγμα.ελ", [])).toBeNull();
    expect(recipientProblem("ops@例え", [])).not.toBeNull();
    expect(recipientProblem("ops@.jp", [])).not.toBeNull();
  });
});

describe("slack channels", () => {
  it("accept only a hooks.slack.com URL with a path and no spaces", () => {
    expect(isSlackUrl("https://hooks.slack.com/services/T0/B0/xyz")).toBe(true);
    expect(isSlackUrl("https://hooks.slack.com/")).toBe(false);
    expect(isSlackUrl("http://hooks.slack.com/services/x")).toBe(false);
    expect(isSlackUrl("https://hooks.slack.com.evil.io/x")).toBe(false);
    expect(isSlackUrl("https://hooks.slack.com/services/a b")).toBe(false);
    expect(isSlackUrl("https://hooks.slack.com/services/é")).toBe(false);
  });

  it("trim the pasted URL before checking it, as the server does", () => {
    const input = values({ kind: "slack", slackUrl: "  https://hooks.slack.com/services/x  " });
    expect(problems("create", input)).toEqual({});
  });

  it("require the URL on create but keep the stored one when blank on edit", () => {
    expect(problems("create", values({ kind: "slack" }))).toHaveProperty("slackUrl");
    expect(problems("edit", values({ kind: "slack" }))).toEqual({});
  });

  it("reject a URL longer than 2048 characters", () => {
    const url = `https://hooks.slack.com/${"x".repeat(2048)}`;
    expect(problems("create", values({ kind: "slack", slackUrl: url }))).toHaveProperty("slackUrl");
  });
});

describe("webhook channels", () => {
  it("accept http(s) URLs with a host and no credentials", () => {
    expect(isWebUrl("https://example.com/hook")).toBe(true);
    expect(isWebUrl("http://10.0.0.5:8080/hook")).toBe(true);
    expect(isWebUrl("https://user:pass@example.com/hook")).toBe(false);
    expect(isWebUrl("ftp://example.com/hook")).toBe(false);
    expect(isWebUrl("javascript:alert(1)")).toBe(false);
    expect(isWebUrl("example.com/hook")).toBe(false);
  });

  it("need a URL even on edit (it is not a secret)", () => {
    expect(problems("edit", values({ kind: "webhook" }))).toHaveProperty("webhookUrl");
  });
});

describe("pagerduty channels", () => {
  it("need exactly 32 ASCII letters and digits", () => {
    expect(ROUTING_KEY).toHaveLength(32);
    expect(problems("create", values({ kind: "pagerduty", routingKey: ROUTING_KEY }))).toEqual({});
    expect(
      problems("create", values({ kind: "pagerduty", routingKey: ROUTING_KEY.slice(1) })),
    ).toHaveProperty("routingKey");
    expect(
      problems("create", values({ kind: "pagerduty", routingKey: "é".repeat(32) })),
    ).toHaveProperty("routingKey");
  });

  it("trim a pasted key", () => {
    const input = values({ kind: "pagerduty", routingKey: ` ${ROUTING_KEY}\n` });
    expect(problems("create", input)).toEqual({});
  });

  it("keep the stored key when the field is blank on edit", () => {
    expect(problems("edit", values({ kind: "pagerduty" }))).toEqual({});
    expect(problems("create", values({ kind: "pagerduty" }))).toHaveProperty("routingKey");
  });
});

describe("request bodies", () => {
  it("send only the chosen kind's fields on create", () => {
    const form = values({
      kind: "webhook",
      webhookUrl: "https://example.com/hook",
      recipients: ["stale@acme.com"],
      slackUrl: "https://hooks.slack.com/old",
    });
    expect(toChannelCreate(form)).toEqual({
      kind: "webhook",
      name: "On-call",
      config: { url: "https://example.com/hook" },
    });
  });

  it("send nothing on an untouched edit, and never a blank secret", () => {
    const stored = channel({
      kind: "pagerduty",
      config: { severity: "warning" },
      has_secret: true,
    });
    const form = values({ kind: "pagerduty", severity: "warning", routingKey: "" });
    expect(toChannelUpdate(form, stored)).toEqual({});
  });

  it("send a new secret and changed config on edit", () => {
    const stored = channel({
      kind: "pagerduty",
      config: { severity: "warning" },
      has_secret: true,
    });
    const form = values({ kind: "pagerduty", severity: "critical", routingKey: ROUTING_KEY });
    expect(toChannelUpdate(form, stored)).toEqual({
      config: { severity: "critical" },
      secret: ROUTING_KEY,
    });
  });

  it("send the recipient list when its order or content changes", () => {
    const stored = channel({ config: { to: ["a@acme.com", "b@acme.com"] } });
    const reordered = values({ recipients: ["b@acme.com", "a@acme.com"] });
    expect(toChannelUpdate(reordered, stored)).toEqual({
      config: { to: ["b@acme.com", "a@acme.com"] },
    });
  });
});

describe("server field errors map onto the form", () => {
  it("routes each server path to the field the user can fix", () => {
    expect(channelFieldOf("config.to.3", "email")).toBe("recipients");
    expect(channelFieldOf("config.url", "webhook")).toBe("webhookUrl");
    expect(channelFieldOf("secret", "slack")).toBe("slackUrl");
    expect(channelFieldOf("secret", "pagerduty")).toBe("routingKey");
    expect(channelFieldOf("secret", "webhook")).toBeNull();
    expect(channelFieldOf("kind", "email")).toBeNull();
  });
});
