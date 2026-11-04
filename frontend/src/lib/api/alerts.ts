import { api } from "./client";
import { orgPath, projectPath } from "./paths";
import type {
  AlertChannel,
  AlertChannelCreate,
  AlertChannelUpdate,
  AlertChannelWithSecret,
  AlertEvent,
  AlertEventQuery,
  AlertPreview,
  AlertRule,
  AlertRulePreviewInput,
  AlertRuleInput,
  ChannelTestResult,
  Delivery,
  DeliveryQuery,
  Page,
} from "./types";

function channelsPath(orgId: string): string {
  return `${orgPath(orgId)}/alert-channels`;
}

function channelPath(orgId: string, channelId: string): string {
  return `${channelsPath(orgId)}/${encodeURIComponent(channelId)}`;
}

function rulesPath(projectId: string): string {
  return `${projectPath(projectId)}/alert-rules`;
}

function rulePath(projectId: string, ruleId: string): string {
  return `${rulesPath(projectId)}/${encodeURIComponent(ruleId)}`;
}

function eventsPath(projectId: string): string {
  return `${projectPath(projectId)}/alert-events`;
}

/**
 * Alert channels (per org), rules and events (per project). Reads need `org:read` or
 * `project:read`; every write needs `alerts:write` (admin).
 */
export const alertsApi = {
  channels: (orgId: string): Promise<AlertChannel[]> =>
    api.get<AlertChannel[]>(channelsPath(orgId)),
  channel: (orgId: string, channelId: string): Promise<AlertChannel> =>
    api.get<AlertChannel>(channelPath(orgId, channelId)),
  /** A webhook's answer carries its signing secret, once. */
  createChannel: (orgId: string, input: AlertChannelCreate): Promise<AlertChannelWithSecret> =>
    api.post<AlertChannelWithSecret>(channelsPath(orgId), input),
  /** With `rotate_secret` the answer carries the new webhook signing secret, once. */
  updateChannel: (
    orgId: string,
    channelId: string,
    update: AlertChannelUpdate,
  ): Promise<AlertChannelWithSecret> =>
    api.patch<AlertChannelWithSecret>(channelPath(orgId, channelId), update),
  /** Rules that name the channel keep its id and skip it until they are edited. */
  deleteChannel: (orgId: string, channelId: string): Promise<void> =>
    api.delete(channelPath(orgId, channelId)),
  /**
   * Sends a test now. A failing channel is still a 200 with `status: "failed"`; `429
   * RATE_LIMITED` after 10 tests an hour. Reuse the key when retrying the same click.
   */
  testChannel: (
    orgId: string,
    channelId: string,
    idempotencyKey?: string,
  ): Promise<ChannelTestResult> =>
    api.post<ChannelTestResult>(
      `${channelPath(orgId, channelId)}/test`,
      undefined,
      idempotencyKey ? { "Idempotency-Key": idempotencyKey } : undefined,
    ),

  /** The channel's notifications, newest first, one per email recipient. Keyset-paginated. */
  deliveries: (
    orgId: string,
    channelId: string,
    query: DeliveryQuery = {},
  ): Promise<Page<Delivery>> =>
    api.get<Page<Delivery>>(`${channelPath(orgId, channelId)}/deliveries`, { ...query }),
  /** Only failed deliveries retry (`409 NOT_RETRYABLE` otherwise). */
  retryDelivery: (orgId: string, channelId: string, deliveryId: string): Promise<Delivery> =>
    api.post<Delivery>(
      `${channelPath(orgId, channelId)}/deliveries/${encodeURIComponent(deliveryId)}/retry`,
    ),

  /** Firing rules first, then by name. Unpaginated; budget rules are not listed. */
  rules: (projectId: string, signal?: AbortSignal): Promise<AlertRule[]> =>
    api.get<AlertRule[]>(rulesPath(projectId), undefined, signal),
  rule: (projectId: string, ruleId: string): Promise<AlertRule> =>
    api.get<AlertRule>(rulePath(projectId, ruleId)),
  createRule: (projectId: string, input: AlertRuleInput): Promise<AlertRule> =>
    api.post<AlertRule>(rulesPath(projectId), input),
  /** Sends the whole rule again; its kind may change. */
  updateRule: (projectId: string, ruleId: string, input: AlertRuleInput): Promise<AlertRule> =>
    api.patch<AlertRule>(rulePath(projectId, ruleId), input),
  deleteRule: (projectId: string, ruleId: string): Promise<void> =>
    api.delete(rulePath(projectId, ruleId)),
  /** `until` must be in the future and at most 30 days ahead; `null` unmutes. */
  muteRule: (projectId: string, ruleId: string, until: string | null): Promise<AlertRule> =>
    api.post<AlertRule>(`${rulePath(projectId, ruleId)}/mute`, { until }),
  /** The last 7 days the rule would have measured. `429 RATE_LIMITED` after 30 a minute. */
  previewRule: (projectId: string, definition: AlertRulePreviewInput): Promise<AlertPreview> =>
    api.post<AlertPreview>(`${rulesPath(projectId)}/preview`, definition),

  /** Newest first. */
  events: (projectId: string, query: AlertEventQuery = {}): Promise<Page<AlertEvent>> =>
    api.get<Page<AlertEvent>>(eventsPath(projectId), { ...query }),
  /** `409 ALREADY_ACKNOWLEDGED` the second time. */
  acknowledgeEvent: (projectId: string, eventId: string): Promise<AlertEvent> =>
    api.post<AlertEvent>(`${eventsPath(projectId)}/${encodeURIComponent(eventId)}/acknowledge`),
};
