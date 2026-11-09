import { zodResolver } from "@hookform/resolvers/zod";
import { useEffect } from "react";
import { useForm } from "react-hook-form";
import { toast } from "sonner";

import { Callout } from "@/components/callout";
import { DisabledReason } from "@/components/disabled-reason";
import { MemberReauthFields } from "@/features/settings/member-reauth";
import { useMemberReauth } from "@/features/settings/use-member-reauth";
import { DialogHeading } from "@/components/dialog-heading";
import { Button } from "@/components/ui/button";
import { DialogClose, DialogFooter } from "@/components/ui/dialog";
import { type CreatedGatewayKey } from "@/lib/api";

import { useGatewayRoutesQuery } from "../gateway-queries";
import { KeyFields } from "./key-fields";
import { applyKeyProblem } from "./key-form-errors";
import { EMPTY_KEY_FORM, keyFormSchema, toCreateInput, type KeyFormValues } from "./key-form";
import { useCreateGatewayKey } from "./keys-queries";
import { useDraftProblems } from "./use-draft-problems";

interface CreateKeyFormProps {
  /** Called as the request starts and ends, so the dialog can refuse to close in between. */
  onPendingChange: (pending: boolean) => void;
  /** Hands over the created key, secret included. The caller keeps it in state only. */
  onCreated: (key: CreatedGatewayKey, routeName: string | null) => void;
}

/** Figma "Create key dialog": name, environment, route, limits, allowed models, tags and cache. */
export function CreateKeyForm({ onCreated, onPendingChange }: CreateKeyFormProps) {
  const createKey = useCreateGatewayKey();
  const confirmation = useMemberReauth();
  const { reauth, formError } = confirmation;
  const routesQuery = useGatewayRoutesQuery();
  const routes = routesQuery.data;
  const pending = createKey.pending;

  // A request that outlives the dialog would make a credential nobody sees.
  useEffect(() => {
    onPendingChange(pending);
    return () => {
      onPendingChange(false);
    };
  }, [pending, onPendingChange]);

  const drafts = useDraftProblems();
  const form = useForm<KeyFormValues>({
    resolver: zodResolver(keyFormSchema),
    defaultValues: { ...EMPTY_KEY_FORM, environment: "development" },
  });

  // Start on the project's default route, once the routes are in and nothing was picked yet.
  const defaultRouteId = routes?.find((route) => route.is_default)?.id;
  useEffect(() => {
    if (defaultRouteId && form.getValues("routeId") === "") {
      form.setValue("routeId", defaultRouteId);
    }
  }, [defaultRouteId, form]);

  const onSubmit = form.handleSubmit(async (values) => {
    if (drafts.blockSave(form.setError)) {
      return;
    }
    if (pending) return;
    const proof = confirmation.proof(confirmation.form.getValues());
    if (!proof) return;
    const result = await createKey.run({ ...toCreateInput(values), ...proof });
    if (result.ok) {
      const routeName = routes?.find((route) => route.id === result.value.route_id)?.name ?? null;
      onCreated(result.value, routeName);
      toast.success(`Created gateway key "${result.value.name}".`);
    } else if (!applyKeyProblem(result.error, form.setError)) {
      confirmation.onError(result.error);
    }
  });

  const createButton = (
    <Button
      type="submit"
      variant="primary"
      loading={pending}
      disabled={routes?.length === 0 || reauth}
    >
      Create key
    </Button>
  );

  return (
    <form onSubmit={onSubmit} noValidate className="grid gap-[22px]">
      <DialogHeading
        showClose={pending ? "disabled" : true}
        title="Create gateway key"
        description="A gateway key routes calls through Spanlight and sets limits for one app or environment."
      />
      <KeyFields
        form={form}
        routes={routes}
        routesFailed={routesQuery.isError}
        onRetryRoutes={() => {
          void routesQuery.refetch();
        }}
        reportDraft={drafts.report}
        layout="dialog"
        currentCacheTtl={null}
      />
      <p aria-live="polite" className="empty:sr-only">
        <span className="text-sm font-semibold text-foreground">Confirm it's you.</span>
        <span className="text-xs text-muted-foreground">
          {
            " A gateway key spends your organization's provider credentials for as long as it lives."
          }
        </span>
      </p>
      <MemberReauthFields
        confirmation={confirmation}
        pending={pending}
        action="Creating a gateway key"
      />
      {formError ? (
        <Callout tone="danger" role="alert">
          {formError}
        </Callout>
      ) : null}
      <DialogFooter className="flex-row justify-end gap-2.5 max-sm:[&>*]:flex-1">
        <DialogClose asChild>
          <Button disabled={pending}>Cancel</Button>
        </DialogClose>
        {reauth ? (
          <DisabledReason reason="Sign in again first.">{createButton}</DisabledReason>
        ) : (
          createButton
        )}
      </DialogFooter>
    </form>
  );
}
