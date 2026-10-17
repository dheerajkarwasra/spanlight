import { CircleAlert, Lock, Plus, X } from "lucide-react";
import { useId, useState } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { addCategoryProblem } from "@/features/scores";

interface SetupCategoriesStepProps {
  categories: readonly string[];
  onChange: (categories: string[]) => void;
}

/**
 * Step 1, "Failure categories": the starting list as removable chips, `none` locked, and a box to
 * add one. Skipped when the project already has a failure_category config.
 */
export function SetupCategoriesStep({ categories, onChange }: SetupCategoriesStepProps) {
  const [draft, setDraft] = useState("");
  const [error, setError] = useState<string | null>(null);
  const inputId = useId();
  const errorId = useId();
  const titleId = useId();

  function add() {
    const value = draft.trim();
    const problem = addCategoryProblem(value, categories);
    setError(problem);
    if (problem === null) {
      onChange([...categories, value]);
      setDraft("");
    }
  }

  return (
    <section className="flex flex-col gap-4" aria-labelledby={titleId}>
      <div className="flex flex-col gap-1">
        <h3 id={titleId} className="text-base font-bold text-foreground">
          Failure categories
        </h3>
        <p className="text-sm text-muted-foreground">
          Reviewers label each trace with one category. Start from this list and change it to match
          what your notes say.
        </p>
      </div>
      <ul className="flex flex-wrap gap-2" aria-label="Categories">
        {categories.map((category) => (
          <li
            key={category}
            className="inline-flex max-w-full items-center gap-1.5 rounded-full border border-border bg-surface px-3 py-1 font-mono text-xs text-foreground"
          >
            {category === "none" ? <Lock aria-hidden className="size-3 shrink-0" /> : null}
            <span className="[overflow-wrap:anywhere]">{category}</span>
            {category === "none" ? null : (
              <button
                type="button"
                aria-label={`Remove ${category}`}
                className="-mr-1 rounded-full p-0.5 text-muted-foreground hover:bg-surface-hover hover:text-foreground"
                onClick={() => onChange(categories.filter((item) => item !== category))}
              >
                <X aria-hidden className="size-3" />
              </button>
            )}
          </li>
        ))}
      </ul>
      <div className="flex flex-col gap-2">
        <Label htmlFor={inputId}>Add a category</Label>
        <div className="flex gap-2">
          <Input
            id={inputId}
            value={draft}
            placeholder="e.g. wrong_tool"
            autoComplete="off"
            spellCheck={false}
            className="min-w-0 flex-1 font-mono"
            aria-invalid={error === null ? undefined : true}
            aria-describedby={error === null ? undefined : errorId}
            onChange={(event) => {
              setDraft(event.target.value);
              setError(null);
            }}
            onKeyDown={(event) => {
              if (event.key === "Enter") {
                event.preventDefault();
                add();
              }
            }}
          />
          <Button type="button" onClick={add}>
            <Plus aria-hidden />
            Add
          </Button>
        </div>
        {error === null ? null : (
          <p id={errorId} className="flex items-start gap-1.5 text-xs font-medium text-danger-text">
            <CircleAlert aria-hidden className="mt-px size-3.5 shrink-0" />
            {error}
          </p>
        )}
      </div>
      <p className="flex items-start gap-1.5 text-xs font-medium text-muted-foreground">
        <Lock aria-hidden className="mt-px size-3.5 shrink-0" />
        <span>
          <code className="font-mono">none</code> cannot be removed: a reviewed trace that passed
          needs a label. 2 to 30 categories.
        </span>
      </p>
    </section>
  );
}
