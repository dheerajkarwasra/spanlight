import { zodResolver } from "@hookform/resolvers/zod";
import { useForm } from "react-hook-form";
import { toast } from "sonner";

import { FormField } from "@/components/form-field";
import { SectionCard } from "@/components/section-card";
import { Input } from "@/components/ui/input";
import { errorMessage, isApiError, type Project } from "@/lib/api";
import { applyMappedFieldErrors } from "@/lib/form-errors";

import {
  changedProjectFields,
  isEmptyUpdate,
  PROJECT_NAME_MAX_LENGTH,
  projectSavedMessage,
  projectSettingsSchema,
  RETENTION_MAX_DAYS,
  RETENTION_MIN_DAYS,
  type ProjectSettingsValues,
} from "./project-settings-schema";
import { InsightChannelsField } from "./insight-channels-field";
import {
  CapturePayloadsField,
  ProjectIdentifiers,
  WeeklyDigestField,
} from "./project-settings-parts";
import { PromotedKeysField } from "./promoted-keys-field";
import { ReadOnlySlugField } from "./read-only-slug-field";
import { SettingsSaveBar } from "./settings-save-bar";
import { useUpdateProject } from "./use-update-project";

interface ProjectSettingsFormProps {
  project: Project;
  canEdit: boolean;
}

// `promoted_metadata_keys.2` (one refused key) lands on the whole list.
const SERVER_FIELDS = {
  name: "name",
  retention_days: "retention_days",
  promoted_metadata_keys: "promoted_metadata_keys",
} as const;

function valuesFrom(project: Project): ProjectSettingsValues {
  return {
    name: project.name,
    retention_days: project.retention_days,
    capture_payloads: project.capture_payloads,
    weekly_digest_enabled: project.weekly_digest_enabled ?? true,
    insight_channel_ids: project.insight_channel_ids,
    promoted_metadata_keys: project.promoted_metadata_keys,
  };
}

/**
 * The project cards (General, Data, Email, Insight notifications, Attribution) and their save
 * bar. One form, so one "Save changes" sends only the fields that differ from the saved project.
 */
export function ProjectSettingsForm({ project, canEdit }: ProjectSettingsFormProps) {
  const updateProject = useUpdateProject();
  const form = useForm<ProjectSettingsValues>({
    resolver: zodResolver(projectSettingsSchema),
    defaultValues: valuesFrom(project),
  });
  const { errors, isDirty } = form.formState;

  const onSubmit = form.handleSubmit((values) => {
    const update = changedProjectFields(project, values);
    if (isEmptyUpdate(update)) {
      form.reset(valuesFrom(project));
      return;
    }
    updateProject.mutate(update, {
      onSuccess: (updated) => {
        form.reset(valuesFrom(updated));
        toast.success(projectSavedMessage(update));
      },
      onError: (error) => {
        if (isApiError(error) && error.code === "UNKNOWN_CHANNEL") {
          form.setError("insight_channel_ids", {
            type: "server",
            message: "A selected channel no longer exists. Remove it and save again.",
          });
        } else if (!applyMappedFieldErrors(error, form.setError, SERVER_FIELDS)) {
          toast.error(errorMessage(error));
        }
      },
    });
  });

  return (
    <form onSubmit={onSubmit} noValidate className="flex flex-col gap-5">
      <SectionCard
        title="General"
        description="Shown in the project switcher and the command palette."
      >
        <div className="grid gap-5 sm:grid-cols-2">
          <FormField
            label="Name"
            hint={canEdit ? `Up to ${PROJECT_NAME_MAX_LENGTH} characters.` : undefined}
            error={errors.name?.message}
          >
            <Input
              autoComplete="off"
              maxLength={PROJECT_NAME_MAX_LENGTH}
              disabled={!canEdit}
              {...form.register("name")}
            />
          </FormField>
          <ReadOnlySlugField
            slug={project.slug}
            hint="Read-only. Used in API URLs and can't be changed."
          />
        </div>
        <ProjectIdentifiers project={project} />
      </SectionCard>

      <SectionCard
        title="Data"
        description="Control how long traces are kept and what is stored with them."
      >
        <div className="flex flex-col gap-4 sm:flex-row sm:gap-6">
          <FormField
            label="Retention period (days)"
            hint={canEdit ? `${RETENTION_MIN_DAYS}–${RETENTION_MAX_DAYS} days` : undefined}
            error={errors.retention_days?.message}
            className="sm:w-[220px] sm:shrink-0"
          >
            <Input
              type="number"
              inputMode="numeric"
              min={RETENTION_MIN_DAYS}
              max={RETENTION_MAX_DAYS}
              step={1}
              disabled={!canEdit}
              className="tabular"
              {...form.register("retention_days", { valueAsNumber: true })}
            />
          </FormField>
          <div className="flex min-w-0 flex-1 flex-col gap-1 sm:pt-[30px]">
            <p className="text-sm text-muted-foreground">
              Traces older than this are deleted automatically, in batches, every hour.
            </p>
            <p className="text-xs font-medium text-muted-foreground">
              Shortening the period deletes older traces within the next hour.
            </p>
          </div>
        </div>
        <hr className="border-border" />
        <CapturePayloadsField control={form.control} canEdit={canEdit} />
      </SectionCard>

      <SectionCard title="Email" description="What members of this project get by email.">
        <WeeklyDigestField control={form.control} canEdit={canEdit} />
      </SectionCard>

      <SectionCard
        title="Insight notifications"
        description="Where the Doctor sends critical findings for this project."
      >
        <InsightChannelsField control={form.control} canEdit={canEdit} />
      </SectionCard>

      <SectionCard
        title="Attribution"
        description="Keys you can group by over windows longer than 24 hours, on Attribution and in exports."
        className="gap-4"
      >
        <PromotedKeysField control={form.control} canEdit={canEdit} />
      </SectionCard>

      {canEdit ? (
        <SettingsSaveBar
          hint="Payload capture changes apply to spans ingested after you save."
          dirty={isDirty}
          saving={updateProject.isPending}
        />
      ) : null}
    </form>
  );
}
