import { useId } from "react";
import { Controller, type Control } from "react-hook-form";

import { ReadOnlyLine } from "@/components/read-only-line";
import { Label } from "@/components/ui/label";

import type { ProjectSettingsValues } from "./project-settings-schema";
import { PromotedKeysInput } from "./promoted-keys-input";
import { MAX_PROMOTED_KEYS } from "./promoted-keys";

export const PROMOTED_KEYS_NOTE =
  "Promoting a key starts a daily rollup; the last 7 days are backfilled. Windows of 24 hours or less work for any key.";

interface PromotedKeysFieldProps {
  control: Control<ProjectSettingsValues>;
  canEdit: boolean;
}

/**
 * Figma "Settings / Project / Promoted keys": the metadata keys that get a daily rollup, as
 * chips. Admins and owners add and remove keys; members and viewers see the list read-only.
 */
export function PromotedKeysField({ control, canEdit }: PromotedKeysFieldProps) {
  const inputId = useId();
  const labelId = useId();
  const countId = useId();
  const noteId = useId();

  return (
    <Controller
      control={control}
      name="promoted_metadata_keys"
      render={({ field, fieldState }) => (
        <div className="flex flex-col gap-2">
          <div className="flex items-center justify-between gap-2">
            {/* At the cap the text box is gone; the chip list is labelled by id instead. */}
            <Label
              id={labelId}
              htmlFor={canEdit && field.value.length < MAX_PROMOTED_KEYS ? inputId : undefined}
              className="text-sm"
            >
              Promoted keys
            </Label>
            <span id={countId} className="text-xs font-medium text-muted-foreground tabular">
              {field.value.length} of {MAX_PROMOTED_KEYS}
            </span>
          </div>
          {canEdit ? (
            <PromotedKeysInput
              id={inputId}
              labelId={labelId}
              keys={field.value}
              onChange={field.onChange}
              serverError={fieldState.error?.message}
              describedBy={`${countId} ${noteId}`}
            />
          ) : (
            <ReadOnlyKeys keys={field.value} />
          )}
          <p id={noteId} className="text-xs font-medium text-muted-foreground">
            {PROMOTED_KEYS_NOTE}
          </p>
        </div>
      )}
    />
  );
}

function ReadOnlyKeys({ keys }: { keys: readonly string[] }) {
  return (
    <>
      <div className="flex min-h-12 flex-wrap items-center gap-1.5 rounded-input border border-border bg-surface-muted px-2.5 py-2">
        {keys.length === 0 ? (
          <span className="px-1 text-sm text-muted-foreground">No keys promoted yet.</span>
        ) : (
          <ul aria-label="Promoted keys" className="flex flex-wrap gap-1.5">
            {keys.map((key) => (
              <li
                key={key}
                className="rounded-full border border-border bg-surface-muted px-2.5 py-[5px] font-mono text-label text-foreground"
              >
                {key}
              </li>
            ))}
          </ul>
        )}
      </div>
      <ReadOnlyLine>Only admins and owners can change promoted keys.</ReadOnlyLine>
    </>
  );
}
