import { useId } from "react";
import { Controller, type Control } from "react-hook-form";

import { CopyButton } from "@/components/copy-button";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import type { Project } from "@/lib/api";
import { formatDate } from "@/lib/format";

import type { ProjectSettingsValues } from "./project-settings-schema";

interface CapturePayloadsFieldProps {
  control: Control<ProjectSettingsValues>;
  canEdit: boolean;
}

/** The "Capture prompts & completions" switch tile of the Data card. */
export function CapturePayloadsField({ control, canEdit }: CapturePayloadsFieldProps) {
  const switchId = useId();
  const descriptionId = useId();

  return (
    <div className="flex items-start gap-4 border-t border-border pt-4">
      <div className="flex min-w-0 flex-1 flex-col gap-1">
        <Label htmlFor={switchId} className="text-sm">
          Capture prompts &amp; completions
        </Label>
        <p id={descriptionId} className="text-sm text-muted-foreground">
          Secrets such as API keys are redacted before storage. Turn off to keep metadata only:
          tokens, cost, latency and model. Existing traces are unaffected.
        </p>
      </div>
      <Controller
        control={control}
        name="capture_payloads"
        render={({ field }) => (
          <Switch
            id={switchId}
            aria-describedby={descriptionId}
            checked={field.value}
            onCheckedChange={field.onChange}
            onBlur={field.onBlur}
            ref={field.ref}
            disabled={!canEdit}
          />
        )}
      />
    </div>
  );
}

/** The "Weekly digest email" switch tile of the Email card. */
export function WeeklyDigestField({ control, canEdit }: CapturePayloadsFieldProps) {
  const switchId = useId();
  const descriptionId = useId();

  return (
    <div className="flex items-start gap-4 border-t border-border pt-4">
      <div className="flex min-w-0 flex-1 flex-col gap-1">
        <Label htmlFor={switchId} className="text-sm">
          Weekly digest email
        </Label>
        <p id={descriptionId} className="text-sm text-muted-foreground">
          Every Monday, members get a summary of spend, calls, errors and alerts.
        </p>
      </div>
      <Controller
        control={control}
        name="weekly_digest_enabled"
        render={({ field }) => (
          <Switch
            id={switchId}
            aria-describedby={descriptionId}
            checked={field.value}
            onCheckedChange={field.onChange}
            onBlur={field.onBlur}
            ref={field.ref}
            disabled={!canEdit}
          />
        )}
      />
    </div>
  );
}

/** The project ID for API calls and support requests, and when the project was created. */
export function ProjectIdentifiers({ project }: { project: Project }) {
  return (
    <dl className="flex flex-wrap items-center gap-x-5 gap-y-1 border-t border-border pt-4 text-xs font-medium text-muted-foreground">
      <div className="flex min-w-0 items-center gap-1.5">
        <dt>Project ID</dt>
        <dd className="flex min-w-0 items-center gap-0.5">
          <code className="truncate font-mono text-label text-foreground">{project.id}</code>
          <CopyButton value={project.id} label="Copy project ID" className="size-7" />
        </dd>
      </div>
      <div className="flex items-center gap-1.5">
        <dt>Created</dt>
        <dd className="text-foreground">
          <time dateTime={project.created_at}>{formatDate(project.created_at)}</time>
        </dd>
      </div>
    </dl>
  );
}
