import { getRouteApi } from "@tanstack/react-router";
import { useCallback, useMemo } from "react";

import type { AuditFilters } from "@/lib/api";

import { hasAuditFilters, toAuditFilters, type AuditFilterValues } from "./audit-filters";

const auditRoute = getRouteApi("/_authed/$orgId/$projectId/settings/audit");

/**
 * The audit log's filters, kept in the URL. `values` is what the controls show, `filters` is the
 * query the list and the CSV both send, and a change replaces the history entry so stepping
 * through filters does not flood the back button.
 */
export function useAuditFilters() {
  const search = auditRoute.useSearch();
  const navigate = auditRoute.useNavigate();
  const { action, actor, since, until, outcome, project, via } = search;

  const values: AuditFilterValues = useMemo(
    () => ({ action, actor, since, until, outcome, project, via }),
    [action, actor, since, until, outcome, project, via],
  );
  const filters: AuditFilters = useMemo(() => toAuditFilters(values), [values]);

  const setFilters = useCallback(
    (patch: AuditFilterValues) => {
      void navigate({ search: (prev) => ({ ...prev, ...patch }), replace: true });
    },
    [navigate],
  );

  const clearFilters = useCallback(() => {
    void navigate({
      search: (prev) => ({
        ...prev,
        action: undefined,
        actor: undefined,
        since: undefined,
        until: undefined,
        outcome: undefined,
        project: undefined,
        via: undefined,
      }),
      replace: true,
    });
  }, [navigate]);

  return { values, filters, hasFilters: hasAuditFilters(values), setFilters, clearFilters };
}
