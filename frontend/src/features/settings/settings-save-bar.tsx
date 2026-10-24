import { Button } from "@/components/ui/button";

interface SettingsSaveBarProps {
  /** The line beside the button: what saving covers, or what it leaves alone. */
  hint: string;
  /** There is nothing to save until the form differs from what is stored. */
  dirty: boolean;
  saving: boolean;
}

/**
 * The page-level save bar (Figma "Save bar") under a settings form: a hint on the left and
 * "Save changes" on the right. One bar saves every card of the form above it.
 */
export function SettingsSaveBar({ hint, dirty, saving }: SettingsSaveBarProps) {
  return (
    <div className="flex flex-col-reverse gap-3 sm:flex-row sm:items-center sm:justify-between">
      <p className="text-xs font-medium text-muted-foreground">{hint}</p>
      <Button
        type="submit"
        variant="primary"
        disabled={!dirty}
        loading={saving}
        className="self-end sm:self-auto"
      >
        Save changes
      </Button>
    </div>
  );
}

/** Sits after the save bar, above cards that save on their own (score configs, org security). */
export function SelfSavingHeading({ id }: { id?: string }) {
  return (
    <div className="flex flex-col gap-0.5 pt-1">
      <h2 id={id} className="text-xl font-bold tracking-tight text-foreground">
        Saved as you change them
      </h2>
      <p className="text-xs font-medium text-muted-foreground">
        These settings save on their own; the bar above doesn&rsquo;t cover them.
      </p>
    </div>
  );
}
