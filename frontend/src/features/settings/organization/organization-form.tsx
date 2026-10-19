import { zodResolver } from "@hookform/resolvers/zod";
import { useForm } from "react-hook-form";
import { toast } from "sonner";

import { FormField } from "@/components/form-field";
import { SectionCard } from "@/components/section-card";
import { Input } from "@/components/ui/input";
import { errorMessage, type Org } from "@/lib/api";
import { applyServerFieldErrors } from "@/lib/form-errors";

import { ReadOnlySlugField } from "../read-only-slug-field";
import { SettingsSaveBar } from "../settings-save-bar";
import {
  isNameChanged,
  ORG_NAME_MAX_LENGTH,
  orgNameSchema,
  type OrgNameValues,
} from "./organization-schema";
import { useUpdateOrg } from "./use-update-org";

interface OrganizationFormProps {
  org: Org;
  /** `org:update`: admins and owners. Everyone else sees the name but can't change it. */
  canEdit: boolean;
  /** `org:security`: owners also have the two-factor switch below, which saves by itself. */
  canSecure: boolean;
}

/**
 * The "General" card and the page-level save bar under it (Figma "Settings — Organization"): the
 * name is editable, the slug is not. The two-factor switch is a separate card with its own save,
 * so the bar says so to owners.
 */
export function OrganizationForm({ org, canEdit, canSecure }: OrganizationFormProps) {
  const updateOrg = useUpdateOrg(org.id);
  const form = useForm<OrgNameValues>({
    resolver: zodResolver(orgNameSchema),
    defaultValues: { name: org.name },
  });
  const { errors, isDirty } = form.formState;

  const onSubmit = form.handleSubmit((values) => {
    if (!isNameChanged(org, values)) {
      form.reset({ name: org.name });
      return;
    }
    updateOrg.mutate(
      { name: values.name },
      {
        onSuccess: (updated) => {
          form.reset({ name: updated.name });
          toast.success("Organization name saved.");
        },
        onError: (error) => {
          if (!applyServerFieldErrors(error, form.setError, ["name"])) {
            toast.error(errorMessage(error));
          }
        },
      },
    );
  });

  return (
    <form onSubmit={onSubmit} noValidate className="flex flex-col gap-5">
      <SectionCard title="General" description="How your organization appears to its members.">
        <div className="grid gap-5 sm:grid-cols-2">
          {canEdit ? (
            <FormField
              label="Name"
              hint={`Up to ${ORG_NAME_MAX_LENGTH} characters.`}
              error={errors.name?.message}
            >
              <Input
                autoComplete="off"
                maxLength={ORG_NAME_MAX_LENGTH}
                {...form.register("name")}
              />
            </FormField>
          ) : (
            <div className="flex flex-col gap-2">
              <p className="text-[13px] font-semibold text-foreground">Name</p>
              <p className="text-sm font-medium text-foreground">{org.name}</p>
            </div>
          )}
          <ReadOnlySlugField slug={org.slug} hint="Read-only. Used in URLs and can't be changed." />
        </div>
      </SectionCard>

      {canEdit ? (
        <SettingsSaveBar
          hint={
            canSecure
              ? "The two-factor requirement below saves on its own."
              : "Renaming doesn't change the slug or any URLs."
          }
          dirty={isDirty}
          saving={updateOrg.isPending}
        />
      ) : null}
    </form>
  );
}
