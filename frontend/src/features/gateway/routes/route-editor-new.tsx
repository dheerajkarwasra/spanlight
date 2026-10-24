import { Link, useNavigate } from "@tanstack/react-router";
import { useEffect, useRef } from "react";
import { toast } from "sonner";

import { CrumbButton } from "@/components/breadcrumbs";
import { Callout } from "@/components/callout";
import { ErrorState } from "@/components/error-state";
import { NoAccess } from "@/components/no-access";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { usePermission, useProjectParams } from "@/features/shell";
import { errorMessage } from "@/lib/api";

import { LeaveGuardDialog } from "./leave-guard";
import { RouteEditorForm } from "./route-editor-form";
import { RouteEditorHeader } from "./route-editor-header";
import { RouteEditorSkeleton } from "./route-editor-skeleton";
import { configFromFormValues, newRouteFormValues } from "./route-form";
import { applyRouteFieldErrors } from "./route-form-errors";
import { RouteSaveBar } from "./route-save-bar";
import { isRouteNameTaken, useCreateRoute } from "./routes-queries";
import { saveBarState } from "./save-bar-state";
import { useLeaveGuard } from "./use-leave-guard";
import { useRouteForm } from "./use-route-form";
import { useRouteLookups, type RouteLookups } from "./use-route-lookups";

interface NewRouteEditorProps {
  name: string;
  onCancel: () => void;
}

/**
 * Figma "Gateway — Route editor — new": the editor on a route that doesn't exist yet. It starts
 * with one target on the org's first credential and the server's default policies, and "Create
 * route" saves it as version 1, then opens the saved route.
 */
export function NewRouteEditor({ name, onCancel }: NewRouteEditorProps) {
  const lookups = useRouteLookups();
  const canWrite = usePermission("gateway:write");
  if (!canWrite) {
    return <NoAccess page="New route" permission="gateway:write" />;
  }
  if (!lookups.credentialsLoaded) {
    return lookups.credentialsError ? (
      <ErrorState
        error={lookups.credentialsError}
        title="Couldn't load credentials"
        onRetry={lookups.retryCredentials}
      />
    ) : (
      <RouteEditorSkeleton />
    );
  }
  return <NewRouteForm name={name} onCancel={onCancel} lookups={lookups} />;
}

function NewRouteForm({
  name,
  onCancel,
  lookups,
}: NewRouteEditorProps & { lookups: RouteLookups }) {
  const { orgId, projectId } = useProjectParams();
  const navigate = useNavigate();
  const createRoute = useCreateRoute();
  const { form, errors } = useRouteForm(newRouteFormValues(lookups.credentials[0]?.id ?? null));
  // The whole draft (its name included) exists only here until it is created.
  const guard = useLeaveGuard(true);
  const container = useRef<HTMLDivElement>(null);
  useEffect(() => {
    // The New route dialog's trigger left with the list; start focus at this editor's name.
    container.current?.querySelector<HTMLElement>("h2")?.focus();
  }, []);
  const bar = saveBarState({
    version: null,
    routeName: name,
    dirty: form.formState.isDirty,
    errors,
    conflictVersion: null,
  });

  const submit = form.handleSubmit((values) => {
    createRoute.mutate(
      { name, config: configFromFormValues(values) },
      {
        onSuccess: (route) => {
          toast.success(`Created route "${route.name}".`);
          guard.allowLeave();
          void navigate({
            to: "/$orgId/$projectId/gateway/routes/$routeId",
            params: { orgId, projectId, routeId: route.id },
          });
        },
        onError: (error) => {
          if (isRouteNameTaken(error)) {
            toast.error(`A route named ${name} already exists. Cancel and pick another name.`);
          } else if (!applyRouteFieldErrors(error, form.setError)) {
            toast.error(errorMessage(error));
          }
        },
      },
    );
  });

  return (
    <div ref={container} className="flex flex-col gap-5">
      <RouteEditorHeader
        name={name}
        badges={<Badge size="sm">Not saved</Badge>}
        meta="New route · the name is fixed once it is created"
        parent={<CrumbButton onClick={onCancel}>Routes</CrumbButton>}
      />
      {lookups.credentials.length === 0 ? (
        <Callout
          tone="warning"
          title="No provider credentials"
          action={
            <Button size="sm" asChild>
              <Link to="/$orgId/$projectId/gateway/credentials" params={{ orgId, projectId }}>
                Open credentials
              </Link>
            </Button>
          }
        >
          A route sends calls with a provider credential. Add one in Gateway › Credentials first.
        </Callout>
      ) : null}
      <RouteEditorForm
        form={form}
        onSubmit={submit}
        context={{ credentials: lookups.credentials, keys: [], canWrite: true }}
        footer={
          bar ? (
            <RouteSaveBar state={bar} saving={createRoute.isPending} onCancel={onCancel} />
          ) : null
        }
      />
      <LeaveGuardDialog guard={guard} />
    </div>
  );
}
