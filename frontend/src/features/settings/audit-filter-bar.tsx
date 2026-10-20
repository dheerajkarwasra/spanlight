import { useMemo } from "react";

import { Button } from "@/components/ui/button";
import { useOrgProjectsQuery, useProjectParams } from "@/features/shell";

import { AuditDateFilter } from "./audit-date-filter";
import {
  ACTOR_TYPE_OPTIONS,
  OUTCOME_OPTIONS,
  actionOptions,
  asActorType,
  actorOptions,
  projectOptions,
  withSelected,
} from "./audit-filter-options";
import { FilterSelectPill } from "./audit-filter-pill";
import type { AuditFilterValues } from "./audit-filters";
import { useMembersQuery } from "./member-queries";

interface AuditFilterBarProps {
  values: AuditFilterValues;
  onChange: (patch: AuditFilterValues) => void;
  /** Shows "Clear filters" at the end of the bar; the empty state has its own button instead. */
  showClear: boolean;
  onClear: () => void;
}

/**
 * Action, Actor, Date, Outcome, Project and Actor type pills with "Clear filters" (Figma "Audit log v3"). The
 * choices live in the URL, so the list and the downloads read the same ones.
 */
export function AuditFilterBar({ values, onChange, showClear, onClear }: AuditFilterBarProps) {
  const membersQuery = useMembersQuery();
  const members = membersQuery.data;
  const { orgId } = useProjectParams();
  const projectsQuery = useOrgProjectsQuery(orgId);
  const projectList = projectsQuery.data;

  const actions = useMemo(
    () => withSelected(actionOptions(), values.action, values.action ?? ""),
    [values.action],
  );
  const actors = useMemo(
    () => withSelected(actorOptions(members ?? []), values.actor, "Former member"),
    [members, values.actor],
  );
  const projects = useMemo(
    () => withSelected(projectOptions(projectList ?? []), values.project, "Deleted project"),
    [projectList, values.project],
  );

  return (
    <div role="group" aria-label="Filters" className="flex flex-wrap items-center gap-2 py-1">
      <FilterSelectPill
        label="Action"
        allLabel="All actions"
        value={values.action}
        options={actions}
        onChange={(action) => {
          onChange({ action });
        }}
      />
      <FilterSelectPill
        label="Actor"
        allLabel="All members"
        value={values.actor}
        options={actors}
        onChange={(actor) => {
          onChange({ actor });
        }}
      />
      <AuditDateFilter
        since={values.since}
        until={values.until}
        onApply={(range) => {
          onChange(range);
        }}
      />
      <FilterSelectPill
        label="Outcome"
        allLabel="All outcomes"
        value={values.outcome}
        options={OUTCOME_OPTIONS}
        onChange={(outcome) => {
          onChange({
            outcome: outcome === "success" || outcome === "failure" ? outcome : undefined,
          });
        }}
      />
      <FilterSelectPill
        label="Project"
        allLabel="All projects"
        value={values.project}
        options={projects}
        onChange={(project) => {
          onChange({ project });
        }}
      />
      <FilterSelectPill
        label="Actor type"
        allLabel="All types"
        value={values.via}
        options={ACTOR_TYPE_OPTIONS}
        onChange={(via) => {
          onChange({ via: asActorType(via) });
        }}
      />
      {showClear ? (
        <Button variant="ghost" className="ml-auto" onClick={onClear}>
          Clear filters
        </Button>
      ) : null}
    </div>
  );
}
