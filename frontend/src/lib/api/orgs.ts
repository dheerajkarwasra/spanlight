import { API_PREFIX, api, type DownloadedFile } from "./client";
import { ApiError, ExportTooLargeError } from "./errors";
import { orgPath } from "./paths";
import type {
  AuditEvent,
  AuditIntegrity,
  AuditStream,
  AuditStreamSaved,
  AuditStreamTest,
  CreatedInvite,
  PendingInvite,
  Member,
  Org,
  OrgWithRole,
  Page,
  Project,
  Role,
} from "./types";

/** What the audit log can be narrowed by; the list and the downloads take the same filters. */
export interface AuditFilters {
  /** An exact action name, e.g. `member.remove`. */
  action?: string | undefined;
  actor_id?: string | undefined;
  /** Inclusive. ISO-8601 UTC. */
  from?: string | undefined;
  /** Exclusive. ISO-8601 UTC. */
  to?: string | undefined;
  project_id?: string | undefined;
  outcome?: AuditEvent["outcome"] | undefined;
  actor_type?: NonNullable<AuditEvent["actor_type"]> | undefined;
}

/** `csv` for spreadsheets, `jsonl` (one event object per line) for log tools. */
export type AuditFileFormat = "csv" | "jsonl";

export interface AuditQuery extends AuditFilters {
  limit?: number | undefined;
  cursor?: string | null | undefined;
}

export interface OrgUpdate {
  name?: string;
  /** `409 NOT_CONFIGURED` or `409 TWO_FACTOR_NOT_ENABLED` when turning it on is refused. */
  require_2fa?: boolean;
}

export const orgsApi = {
  create: (name: string): Promise<Org> => api.post<Org>(`${API_PREFIX}/orgs`, { name }),
  get: (orgId: string): Promise<OrgWithRole> => api.get<OrgWithRole>(orgPath(orgId)),
  /** At least one field; `name` needs an admin and `require_2fa` an owner. */
  update: (orgId: string, update: OrgUpdate): Promise<Org> =>
    api.patch<Org>(orgPath(orgId), update),
  /**
   * Owner only. `confirm` is the org's slug exactly as typed; a wrong one is `422
   * CONFIRMATION_MISMATCH`. Deletes its projects, traces, keys, members, invites and audit log.
   */
  delete: (orgId: string, confirm: string): Promise<void> =>
    api.delete(orgPath(orgId), { confirm }),

  projects: (orgId: string): Promise<Project[]> => api.get<Project[]>(`${orgPath(orgId)}/projects`),
  createProject: (orgId: string, name: string): Promise<Project> =>
    api.post<Project>(`${orgPath(orgId)}/projects`, { name }),

  members: (orgId: string): Promise<Member[]> => api.get<Member[]>(`${orgPath(orgId)}/members`),
  updateMemberRole: (orgId: string, userId: string, role: Role): Promise<Member> =>
    api.patch<Member>(`${orgPath(orgId)}/members/${encodeURIComponent(userId)}`, { role }),
  removeMember: (orgId: string, userId: string): Promise<void> =>
    api.delete(`${orgPath(orgId)}/members/${encodeURIComponent(userId)}`),

  invites: (orgId: string): Promise<PendingInvite[]> =>
    api.get<PendingInvite[]>(`${orgPath(orgId)}/invites`),
  /** With `email` and a configured sender the link is also mailed; the answer still carries `url`. */
  createInvite: (orgId: string, role: Role, email?: string): Promise<CreatedInvite> =>
    api.post<CreatedInvite>(`${orgPath(orgId)}/invites`, { role, email }),
  revokeInvite: (orgId: string, inviteId: string): Promise<void> =>
    api.delete(`${orgPath(orgId)}/invites/${encodeURIComponent(inviteId)}`),

  audit: (orgId: string, query: AuditQuery = {}): Promise<Page<AuditEvent>> =>
    api.get<Page<AuditEvent>>(`${orgPath(orgId)}/audit`, { ...query }),
  auditIntegrity: (orgId: string): Promise<AuditIntegrity> =>
    api.get<AuditIntegrity>(`${orgPath(orgId)}/audit/integrity`),
  /** The org's audit stream, or null when none is set up. */
  auditStream: (orgId: string): Promise<AuditStream | null> =>
    api.get<AuditStream | null>(`${orgPath(orgId)}/audit/stream`),
  /** Creates the stream (the answer carries its secret once) or changes its URL or switch. */
  saveAuditStream: (
    orgId: string,
    body: { url: string; enabled: boolean },
  ): Promise<AuditStreamSaved> => api.put<AuditStreamSaved>(`${orgPath(orgId)}/audit/stream`, body),
  rotateAuditStreamSecret: (orgId: string): Promise<AuditStreamSaved> =>
    api.post<AuditStreamSaved>(`${orgPath(orgId)}/audit/stream/rotate-secret`),
  deleteAuditStream: (orgId: string): Promise<void> => api.delete(`${orgPath(orgId)}/audit/stream`),
  testAuditStream: (orgId: string): Promise<AuditStreamTest> =>
    api.post<AuditStreamTest>(`${orgPath(orgId)}/audit/stream/test`),
  /**
   * The audit log as a CSV or JSON Lines file, narrowed by the same filters as the list. Rejects
   * with `ExportTooLargeError` when more than 50 000 events match, before anything is sent.
   */
  downloadAudit: async (
    orgId: string,
    format: AuditFileFormat,
    filters: AuditFilters = {},
  ): Promise<DownloadedFile> => {
    try {
      return await api.download(`${orgPath(orgId)}/audit/export.${format}`, { ...filters });
    } catch (error) {
      if (error instanceof ApiError && error.status === 422 && error.code === "EXPORT_TOO_LARGE") {
        throw new ExportTooLargeError(error);
      }
      throw error;
    }
  },
};
