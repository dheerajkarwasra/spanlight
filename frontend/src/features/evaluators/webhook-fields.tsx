import { Link } from "@tanstack/react-router";
import { Controller, type UseFormReturn } from "react-hook-form";

import { FieldSelect } from "@/components/field-select";
import { FormField } from "@/components/form-field";
import { Notice } from "@/components/notice";
import { SectionCard } from "@/components/section-card";
import { useProjectParams } from "@/features/shell";

import { useAlertChannelsQuery } from "./evaluator-queries";
import type { EvaluatorFormValues } from "./evaluator-schema";

/** Figma "Webhook": the organization's webhook channel the payload is posted to. */
export function WebhookFields({ form }: { form: UseFormReturn<EvaluatorFormValues> }) {
  const { orgId, projectId } = useProjectParams();
  const channels = useAlertChannelsQuery();
  const options = (channels.data ?? [])
    .filter((channel) => channel.kind === "webhook")
    .map((channel) => ({ value: channel.id, label: `${channel.name} · webhook` }));

  return (
    <SectionCard
      title="Webhook"
      description="Spanlight sends the redacted input and output. Your endpoint replies with scores."
      className="gap-4"
    >
      <Controller
        control={form.control}
        name="channelId"
        render={({ field, fieldState }) => (
          <FormField
            label="Channel"
            error={
              fieldState.error?.message ??
              (channels.isError
                ? "The channels couldn't be loaded. Reload to try again."
                : undefined)
            }
            hint={
              <>
                Only webhook channels are listed. Add one under{" "}
                <Link
                  to="/$orgId/$projectId/settings/channels"
                  params={{ orgId, projectId }}
                  className="rounded-xs font-semibold text-accent underline-offset-4 hover:underline"
                >
                  Settings › Notification channels
                </Link>
                .
              </>
            }
          >
            <FieldSelect
              ref={field.ref}
              value={field.value}
              placeholder={options.length === 0 ? "No webhook channels yet" : "Pick a channel"}
              options={options}
              onChange={field.onChange}
            />
          </FormField>
        )}
      />
      <Notice>
        One attempt, 30 second timeout. Redirects are not followed. The body is capped at 256 KB.
      </Notice>
    </SectionCard>
  );
}
