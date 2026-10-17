import { Check, CircleAlert } from "lucide-react";
import { Checkbox as CheckboxPrimitive } from "radix-ui";
import { useId } from "react";

import type { Project } from "@/lib/api";
import { cn } from "@/lib/utils";

interface SourceProjectsFieldProps {
  projects: readonly Project[];
  selected: readonly string[];
  onChange: (selected: string[]) => void;
  /** Project id to the other source of the same provider that already reconciles it. */
  taken: ReadonlyMap<string, string>;
  error: string | undefined;
}

/**
 * Figma "Projects to compare": one "Settings/Checkbox option" per project of the organization. A
 * project another source of the same provider reconciles is offered disabled, "Already reconciled
 * by {source}", because a project is compared with one account per provider.
 */
export function SourceProjectsField({
  projects,
  selected,
  onChange,
  taken,
  error,
}: SourceProjectsFieldProps) {
  const labelId = useId();
  const hintId = useId();
  const errorId = useId();

  function toggle(projectId: string, checked: boolean) {
    const rest = selected.filter((id) => id !== projectId);
    onChange(checked ? [...rest, projectId] : rest);
  }

  return (
    <div
      role="group"
      aria-labelledby={labelId}
      aria-describedby={error ? `${hintId} ${errorId}` : hintId}
      className="flex flex-col gap-2"
    >
      <span id={labelId} className="text-label leading-5 font-semibold text-foreground">
        Projects to compare
      </span>
      {projects.map((project) => {
        const owner = taken.get(project.id);
        return (
          <ProjectOption
            key={project.id}
            project={project}
            checked={selected.includes(project.id)}
            takenBy={owner && !selected.includes(project.id) ? owner : null}
            onCheckedChange={(checked) => {
              toggle(project.id, checked);
            }}
          />
        );
      })}
      <p id={hintId} className="text-xs font-medium text-muted-foreground">
        A project is compared with one source per provider.
      </p>
      {error ? (
        <p id={errorId} className="flex items-start gap-1.5 text-xs font-medium text-danger-text">
          <CircleAlert aria-hidden className="mt-px size-3.5 shrink-0" strokeWidth={2.25} />
          {error}
        </p>
      ) : null}
    </div>
  );
}

interface ProjectOptionProps {
  project: Project;
  checked: boolean;
  /** The source that already reconciles this project, which disables it; null when free. */
  takenBy: string | null;
  onCheckedChange: (checked: boolean) => void;
}

function ProjectOption({ project, checked, takenBy, onCheckedChange }: ProjectOptionProps) {
  const nameId = useId();
  const descriptionId = useId();
  const disabled = takenBy !== null;

  return (
    <label
      className={cn(
        "flex items-start gap-3 rounded-input transition-colors",
        checked
          ? "border-[1.5px] border-accent bg-surface-selected p-[13.5px]"
          : "border border-border-strong bg-surface p-3.5",
        disabled ? "cursor-not-allowed opacity-55" : "cursor-pointer",
        !checked && !disabled && "hover:bg-surface-muted",
      )}
    >
      <span className="flex h-[21px] w-[18px] shrink-0 items-center justify-center">
        <CheckboxPrimitive.Root
          checked={checked}
          // `aria-disabled`, not `disabled`: the option stays focusable, so the reason is read.
          aria-disabled={disabled || undefined}
          onCheckedChange={(next) => {
            if (!disabled) {
              onCheckedChange(next === true);
            }
          }}
          aria-labelledby={nameId}
          aria-describedby={descriptionId}
          className="flex size-[18px] items-center justify-center rounded-checkbox border-[1.5px] border-subtle-foreground bg-surface data-[state=checked]:border-accent data-[state=checked]:bg-accent"
        >
          <CheckboxPrimitive.Indicator>
            <Check aria-hidden className="size-3 text-accent-foreground" strokeWidth={3} />
          </CheckboxPrimitive.Indicator>
        </CheckboxPrimitive.Root>
      </span>
      <span className="flex min-w-0 flex-1 flex-col gap-1">
        <span id={nameId} className="truncate text-sm font-semibold text-foreground">
          {project.name}
        </span>
        <span id={descriptionId} className="text-xs font-medium text-muted-foreground">
          {takenBy ? `Already reconciled by ${takenBy}` : project.slug}
        </span>
      </span>
    </label>
  );
}
