import { Card } from "@/components/ui/card";

import { CardTitle } from "./card-title";

/**
 * Figma "Variables": the `{{name}}` references the template uses, in first-appearance order.
 * `variables` is null while the template can't be read.
 */
export function VariablesPanel({ variables }: { variables: string[] | null }) {
  return (
    <Card className="flex flex-col gap-4 p-5 sm:p-6">
      <CardTitle title="Variables" subtitle="Detected from {{name}} in the template" />
      {variables === null ? (
        <p className="text-[13px] text-subtle-foreground">Fix the template to see its variables.</p>
      ) : variables.length === 0 ? (
        <p className="text-[13px] text-subtle-foreground">No variables found yet.</p>
      ) : (
        <ul className="flex flex-wrap gap-2" aria-label="Variables">
          {variables.map((name) => (
            <li
              key={name}
              className="rounded-full bg-accent-subtle px-2.5 py-1 font-mono text-xs text-accent"
            >
              {name}
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}
