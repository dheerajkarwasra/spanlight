import { useId } from "react";
import { Controller, type Control, type UseFormRegisterReturn } from "react-hook-form";

import { FormField } from "@/components/form-field";
import { DateInput } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";

import type { ProfileFormValues } from "./lab-profile";

interface ProfileScheduleFieldsProps {
  control: Control<ProfileFormValues>;
  expiresAt: UseFormRegisterReturn<"expiresAt">;
  expiresAtError: string | undefined;
}

/** Figma "Enabled" and "Expires at": the switch and the end time, side by side from 640 px. */
export function ProfileScheduleFields({
  control,
  expiresAt,
  expiresAtError,
}: ProfileScheduleFieldsProps) {
  return (
    <div className="grid gap-[22px] sm:grid-cols-2 sm:gap-4">
      <Controller
        control={control}
        name="enabled"
        render={({ field }) => <EnabledField checked={field.value} onChange={field.onChange} />}
      />
      <FormField
        label="Expires at"
        hint="Pre-filled to 24 hours from now. Clear it to keep the profile running."
        error={expiresAtError}
      >
        <DateInput type="datetime-local" {...expiresAt} />
      </FormField>
    </div>
  );
}

function EnabledField({
  checked,
  onChange,
}: {
  checked: boolean;
  onChange: (checked: boolean) => void;
}) {
  const id = useId();
  const labelId = `${id}-label`;
  const hintId = `${id}-hint`;

  return (
    <div className="flex flex-col gap-2">
      <span id={labelId} className="text-label font-semibold text-foreground">
        Enabled
      </span>
      <div className="flex h-[46px] items-center gap-3 rounded-input border border-input bg-surface px-4">
        <Switch
          checked={checked}
          onCheckedChange={onChange}
          aria-labelledby={labelId}
          aria-describedby={hintId}
        />
        <span aria-hidden className="text-sm text-foreground">
          {checked ? "Fires on attached keys" : "Does not fire"}
        </span>
      </div>
      <p id={hintId} className="text-xs font-medium text-muted-foreground">
        Disabled profiles stay attached but never fire.
      </p>
    </div>
  );
}
