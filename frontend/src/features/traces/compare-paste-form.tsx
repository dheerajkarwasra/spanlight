import { useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "@tanstack/react-router";
import { useId, useState, type SubmitEvent } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { useProjectParams } from "@/features/shell/project-context";
import { projectsApi, queryKeys, type TraceSummary } from "@/lib/api";

import { compareSearch } from "./search";

export type ThisTrace = Pick<TraceSummary, "trace_id" | "name" | "started_at">;

/** "Paste a trace ID": any id other than this trace's; an unknown one gets the compare page's not-found state. */
export function PasteForm({ trace }: { trace: ThisTrace }) {
  const inputId = useId();
  const errorId = useId();
  const [value, setValue] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);
  const { orgId, projectId } = useProjectParams();
  const queryClient = useQueryClient();
  const navigate = useNavigate();

  async function handleSubmit(event: SubmitEvent<HTMLFormElement>) {
    event.preventDefault();
    const id = value.trim();
    if (id === "" || id === trace.trace_id) {
      setError(id === "" ? "Paste a trace ID." : "That's this trace. Paste another trace's ID.");
      return;
    }
    setPending(true);
    let [a, b] = [id, trace.trace_id];
    try {
      // The comparison names both start times; the compare page reuses this cached response.
      const comparison = await queryClient.query({
        queryKey: queryKeys.project(projectId).traceComparison(a, b),
        queryFn: () => projectsApi.compareTraces(projectId, a, b),
        staleTime: 60_000,
      });
      if (Date.parse(comparison.a.started_at) > Date.parse(comparison.b.started_at)) {
        [a, b] = [b, a];
      }
    } catch {
      // An unknown id: the compare page shows its not-found state for it.
    }
    setPending(false);
    void navigate({
      from: "/$orgId/$projectId/traces/$traceId",
      to: "/$orgId/$projectId/traces/compare",
      params: { orgId, projectId },
      search: (prev) => compareSearch(prev, a, b),
    });
  }

  return (
    <form
      onSubmit={(event) => void handleSubmit(event)}
      className="flex flex-col gap-1.5"
      noValidate
    >
      <Label htmlFor={inputId}>Paste a trace ID</Label>
      <div className="flex gap-2">
        <Input
          id={inputId}
          value={value}
          autoComplete="off"
          spellCheck={false}
          aria-invalid={error !== null}
          aria-describedby={error !== null ? errorId : undefined}
          className="font-mono text-label"
          onChange={(event) => {
            setValue(event.target.value);
            setError(null);
          }}
        />
        <Button type="submit" size="sm" loading={pending}>
          Compare
        </Button>
      </div>
      {error !== null ? (
        <p id={errorId} role="alert" className="text-xs text-danger-text">
          {error}
        </p>
      ) : null}
    </form>
  );
}
