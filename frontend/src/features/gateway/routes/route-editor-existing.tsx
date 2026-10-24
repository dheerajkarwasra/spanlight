import { useNavigate } from "@tanstack/react-router";
import { History } from "lucide-react";
import { useState } from "react";

import { Crumb } from "@/components/breadcrumbs";
import { ReadOnlyNote } from "@/components/read-only-note";
import { UnknownValue, ValueOrUnknown } from "@/components/unknown-value";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { usePermission, useProjectParams } from "@/features/shell";
import type { Route } from "@/lib/api";
import { formatTimestamp } from "@/lib/format";
import { viewOnlyCopy } from "@/lib/permissions";

import { RouteActionsMenu } from "./route-actions-menu";
import { RouteEditorForm } from "./route-editor-form";
import { RouteEditorHeader } from "./route-editor-header";
import { LeaveGuardDialog } from "./leave-guard";
import { formValuesFromConfig } from "./route-form";
import { RouteSaveBar } from "./route-save-bar";
import { keyCountLabel, userName } from "./route-summary";
import { saveBarState } from "./save-bar-state";
import { useLeaveGuard } from "./use-leave-guard";
import { useRouteForm } from "./use-route-form";
import type { RouteLookups } from "./use-route-lookups";
import { useRouteSave } from "./use-route-save";
import { VersionConflictCallout } from "./version-conflict-callout";
import { VersionHistoryDrawer } from "./version-history-drawer";

interface ExistingRouteEditorProps {
  route: Route;
  lookups: RouteLookups;
}

/**
 * Figma "Gateway — Route editor": edit a saved route. Saving sends the version the form was
 * loaded from, so a concurrent save surfaces as the version-conflict callout; History compares
 * and reverts earlier versions.
 */
export function ExistingRouteEditor({ route, lookups }: ExistingRouteEditorProps) {
  const { orgId, projectId } = useProjectParams();
  const navigate = useNavigate();
  const canWrite = usePermission("gateway:write");
  const { form, errors } = useRouteForm(formValuesFromConfig(route.config));
  const save = useRouteSave(route, form);
  const guard = useLeaveGuard(form.formState.isDirty);
  const [historyOpen, setHistoryOpen] = useState(false);
  const keys = lookups.keysFor(route.id);
  const bar = saveBarState({
    version: save.baseVersion,
    routeName: route.name,
    dirty: form.formState.isDirty,
    errors,
    conflictVersion: save.conflictVersion,
  });
  const backToList = () => {
    guard.allowLeave();
    void navigate({ to: "/$orgId/$projectId/gateway/routes", params: { orgId, projectId } });
  };

  return (
    <div className="flex flex-col gap-5">
      <RouteEditorHeader
        name={route.name}
        badges={
          <>
            {route.is_default ? (
              <Badge variant="lime" size="sm">
                Default
              </Badge>
            ) : null}
            <Badge size="sm">Version {save.baseVersion}</Badge>
          </>
        }
        meta={<SavedLine route={route} keyCount={keys?.length ?? null} />}
        parent={
          <Crumb to="/$orgId/$projectId/gateway/routes" params={{ orgId, projectId }}>
            Routes
          </Crumb>
        }
        actions={
          <>
            <Button
              variant="secondary"
              size="sm"
              onClick={() => {
                setHistoryOpen(true);
              }}
            >
              <History aria-hidden />
              History
            </Button>
            {canWrite ? (
              <RouteActionsMenu
                route={route}
                keyCount={keys?.length ?? null}
                onDeleted={backToList}
              />
            ) : null}
          </>
        }
      />
      {canWrite ? null : (
        <ReadOnlyNote>{viewOnlyCopy("gateway routes", "gateway:write")}</ReadOnlyNote>
      )}
      {save.conflictVersion === null ? null : (
        <VersionConflictCallout
          version={save.conflictVersion}
          reloading={save.reloading}
          onReload={() => {
            void save.reloadLatest();
          }}
        />
      )}
      <RouteEditorForm
        form={form}
        onSubmit={save.submit}
        context={{ credentials: lookups.credentials, keys, canWrite }}
        footer={
          bar && canWrite ? (
            <RouteSaveBar
              state={bar}
              saving={save.saving}
              onCancel={() => {
                // During a conflict the stored config is newer than `route`: discarding loads it.
                if (save.conflictVersion === null) {
                  save.discard();
                } else {
                  void save.reloadLatest();
                }
              }}
            />
          ) : null
        }
      />
      <VersionHistoryDrawer
        route={route}
        open={historyOpen}
        onOpenChange={setHistoryOpen}
        nameOf={lookups.nameOf}
        revert={{ dirty: form.formState.isDirty, onReverted: save.adopt }}
      />
      <LeaveGuardDialog guard={guard} />
    </div>
  );
}

/** "Saved Oct 9, 2026, 14:32 by Dheeraj · used by 3 keys". */
function SavedLine({ route, keyCount }: { route: Route; keyCount: number | null }) {
  return (
    <>
      Saved{" "}
      <ValueOrUnknown
        value={formatTimestamp(route.updated_at)}
        reason="The timestamp could not be read."
      />{" "}
      by{" "}
      {route.updated_by ? (
        userName(route.updated_by)
      ) : (
        <UnknownValue reason="Saved by Spanlight or by a removed account." />
      )}{" "}
      · used by{" "}
      {keyCount === null ? (
        <UnknownValue reason="Gateway keys couldn't be loaded." />
      ) : (
        keyCountLabel(keyCount).toLowerCase()
      )}
    </>
  );
}
