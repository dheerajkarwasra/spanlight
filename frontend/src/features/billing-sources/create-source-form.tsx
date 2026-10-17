import { useEffect } from "react";

import { DialogHeading } from "@/components/dialog-heading";
import { FormField } from "@/components/form-field";
import { Button } from "@/components/ui/button";
import { DialogClose, DialogFooter } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { useCurrentOrg, useOrgProjectsQuery, useProjectParams } from "@/features/shell";
import { errorMessage, type BillingSource } from "@/lib/api";

import { useBillingSourcesQuery } from "./billing-sources-queries";
import { SourceDetailFields } from "./source-detail-fields";
import { SOURCE_NAME_MAX_LENGTH, isApiKind, providerOfForm, takenProjects } from "./source-form";
import { SourceKindField } from "./source-kind-field";
import { SourceProjectsField } from "./source-projects-field";
import { useSourceForm } from "./use-source-form";

interface CreateSourceFormProps {
  /** The source being edited; absent when adding one. */
  source: BillingSource | undefined;
  /** The server has no CREDENTIALS_KEYS: only the CSV kind can be added. */
  apiKindsOff: boolean;
  /** Called as the request starts and ends, so the dialog can refuse to close in between. */
  onPendingChange: (pending: boolean) => void;
  onDone: () => void;
  onNotConfigured: (copy: string) => void;
}

const API_DESCRIPTION =
  "Spanlight fetches the provider’s cost report every day and compares it with its own figures for the projects you pick.";
const CSV_DESCRIPTION =
  "Upload the provider’s cost export whenever you like. Each upload replaces the provider figures for every day from its first to its last day.";

/**
 * Figma "Billing sources / create dialog anthropic", "/ create dialog csv" and "/ not configured":
 * kind, name, the kind's own fields and the projects to compare. Editing reuses it without the
 * kind (a source keeps its kind) and with Rotate key in place of the key field.
 */
export function CreateSourceForm({
  source,
  apiKindsOff,
  onPendingChange,
  onDone,
  onNotConfigured,
}: CreateSourceFormProps) {
  const org = useCurrentOrg();
  const { orgId } = useProjectParams();
  const projectsQuery = useOrgProjectsQuery(orgId);
  const sources = useBillingSourcesQuery().data ?? [];
  const form = useSourceForm({ source, apiKindsOff, onDone, onNotConfigured });
  const { values, errors, update, pending } = form;
  const taken = takenProjects(sources, providerOfForm(values), source?.id ?? null);

  useEffect(() => {
    onPendingChange(pending);
    return () => {
      onPendingChange(false);
    };
  }, [pending, onPendingChange]);

  return (
    <form
      noValidate
      className="grid gap-[22px]"
      onSubmit={(event) => {
        event.preventDefault();
        form.submit();
      }}
    >
      <DialogHeading
        title={source ? `Edit ${source.name}` : "Add billing source"}
        description={isApiKind(values.kind) ? API_DESCRIPTION : CSV_DESCRIPTION}
        showClose={pending ? "disabled" : true}
      />
      {source ? null : (
        <SourceKindField
          value={values.kind}
          apiKindsOff={apiKindsOff}
          onChange={(kind) => {
            update({ kind, adminKey: "", providerScope: "" });
          }}
        />
      )}
      <FormField
        label="Name"
        hint={`Shown in reports. Unique in ${org?.name ?? "your organization"}.`}
        error={errors.name}
      >
        <Input
          autoComplete="off"
          maxLength={SOURCE_NAME_MAX_LENGTH * 2}
          placeholder="Prod account"
          value={values.name}
          onChange={(event) => {
            update({ name: event.target.value });
          }}
        />
      </FormField>
      <SourceDetailFields values={values} errors={errors} update={update} source={source} />
      {projectsQuery.isPending ? (
        <div role="status" aria-label="Loading projects">
          <Skeleton className="h-[73px] rounded-input" />
        </div>
      ) : projectsQuery.isError ? (
        <p role="alert" className="text-xs font-medium text-danger-text">
          Couldn&rsquo;t load the projects: {errorMessage(projectsQuery.error)}
        </p>
      ) : (
        <SourceProjectsField
          projects={projectsQuery.data}
          selected={values.projectIds}
          taken={taken}
          error={errors.projectIds}
          onChange={(projectIds) => {
            update({ projectIds });
          }}
        />
      )}
      <DialogFooter className="flex-row justify-end gap-2.5 max-sm:[&>*]:flex-1">
        <DialogClose asChild>
          <Button disabled={pending}>Cancel</Button>
        </DialogClose>
        <Button type="submit" variant="primary" loading={pending}>
          {source ? "Save changes" : "Add source"}
        </Button>
      </DialogFooter>
    </form>
  );
}
