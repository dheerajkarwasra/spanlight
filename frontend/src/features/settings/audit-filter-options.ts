/** The choices in the audit log's Action and Actor filters. Pure functions only. */
import type { Member, Project } from "@/lib/api";

import { AUDIT_FILTER_ACTIONS, auditActionLabel } from "./audit-actions";

export interface FilterOption {
  value: string;
  label: string;
}

/** Every action the server records, under the name the log shows for it. */
export function actionOptions(): FilterOption[] {
  return AUDIT_FILTER_ACTIONS.map((action) => ({
    value: action,
    label: auditActionLabel(action) ?? action,
  }));
}

/** The two outcomes the server records. */
export const OUTCOME_OPTIONS: FilterOption[] = [
  { value: "success", label: "Succeeded" },
  { value: "failure", label: "Failed" },
];

/** Who can act, as the server records it (`actor_type`). */
export const ACTOR_TYPE_OPTIONS: FilterOption[] = [
  { value: "user", label: "Signed-in person" },
  { value: "token", label: "Access token" },
  { value: "cli", label: "Admin command" },
  { value: "system", label: "System" },
];

const ACTOR_TYPES = new Set(ACTOR_TYPE_OPTIONS.map((option) => option.value));

/** The pill's value as the filter's type, or undefined for "All types". */
export function asActorType(
  value: string | undefined,
): "user" | "token" | "cli" | "system" | undefined {
  return value !== undefined && ACTOR_TYPES.has(value)
    ? (value as "user" | "token" | "cli" | "system")
    : undefined;
}

/** The organization's projects; a deleted one cannot be picked, only read from the URL. */
export function projectOptions(projects: readonly Project[]): FilterOption[] {
  return projects.map((project) => ({ value: project.id, label: project.name }));
}

/** The organization's current members; a person who has left cannot be picked, only read. */
export function actorOptions(members: readonly Member[]): FilterOption[] {
  return members.map((member) => ({
    value: member.user.id,
    label: member.user.name || member.user.email,
  }));
}

/**
 * Keeps a value that came from the URL selectable when it is not among the options (an action this
 * client does not list, a member who has left), so the pill never shows an empty selection.
 */
export function withSelected(
  options: FilterOption[],
  value: string | undefined,
  fallbackLabel: string,
): FilterOption[] {
  if (value === undefined || options.some((option) => option.value === value)) {
    return options;
  }
  return [{ value, label: fallbackLabel }, ...options];
}
