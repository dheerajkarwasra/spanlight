import { z } from "zod";

import { isRealDay } from "./audit-filters";

const DAY_PATTERN = /^\d{4}-\d{2}-\d{2}$/;
const UUID_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/** A calendar day as `<input type="date">` writes it; anything else, or a day off the calendar, is dropped. */
function optionalDay() {
  return z.string().regex(DAY_PATTERN).refine(isRealDay).optional().catch(undefined);
}

/**
 * The search of the Audit log page: the filters it keeps in the URL so a filtered view can be
 * shared and survives a reload. `since` and `until` are local calendar days, both inclusive. They
 * are not called `from` and `to` because those belong to the project's time range, which every
 * settings page keeps in its URL. A value that cannot be a filter (an `actor` that is not an id, a
 * day that does not exist, a range that ends before it starts) is dropped here, so it is neither
 * sent to the API (which would answer 422) nor shown as a pill.
 */
export const auditSearchSchema = z
  .object({
    /** An exact audit action, e.g. `member.remove`. */
    action: z.string().optional().catch(undefined),
    /** The id of the member who acted. */
    actor: z.string().regex(UUID_PATTERN).optional().catch(undefined),
    since: optionalDay(),
    until: optionalDay(),
    outcome: z.enum(["success", "failure"]).optional().catch(undefined),
    /** The id of a project: only events written in it. */
    project: z.string().regex(UUID_PATTERN).optional().catch(undefined),
    /** Who acted: `user`, `token`, `cli` or `system`. */
    via: z.enum(["user", "token", "cli", "system"]).optional().catch(undefined),
  })
  .transform((search) =>
    search.since !== undefined && search.until !== undefined && search.since > search.until
      ? { ...search, since: undefined, until: undefined }
      : search,
  );

export type AuditSearch = z.infer<typeof auditSearchSchema>;
