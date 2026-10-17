import { Link, useNavigate } from "@tanstack/react-router";
import { Activity } from "lucide-react";

import { EmptyState } from "@/components/empty-state";
import { ErrorState } from "@/components/error-state";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { useProjectParams } from "@/features/shell/project-context";
import type { RangePreset, ResolvedRange } from "@/lib/time-range";

/** Figma `Providers/Page — loading`: skeletons in the final card shapes (static under reduced motion). */
export function ProvidersSkeleton() {
  return (
    <div aria-hidden className="flex flex-col gap-6">
      <div className="flex flex-wrap gap-4">
        {[0, 1].map((key) => (
          <Card
            key={key}
            className="flex min-w-[min(100%,18rem)] flex-[2_1_18rem] flex-col gap-3 p-6"
          >
            <Skeleton className="h-4 w-28" />
            <Skeleton className="h-3 w-20" />
            <Skeleton className="h-11 rounded-tile" />
            <Skeleton className="h-11 rounded-tile" />
          </Card>
        ))}
        <Card className="flex min-w-[min(100%,13rem)] flex-[1_1_13rem] flex-col gap-3 p-6">
          <Skeleton className="h-3 w-20" />
          <Skeleton className="h-7 w-32 rounded-tile" />
          <Skeleton className="h-3 w-36" />
        </Card>
      </div>
      <Card className="flex flex-col gap-3 p-6">
        <Skeleton className="h-4 w-36" />
        <Skeleton className="h-3 w-72 max-w-full" />
        <Skeleton className="h-[220px] rounded-tile" />
      </Card>
      <Card className="flex flex-col gap-3 p-6">
        <Skeleton className="h-4 w-36" />
        <Skeleton className="h-10 rounded-tile" />
        <Skeleton className="h-10 rounded-tile" />
        <Skeleton className="h-10 rounded-tile" />
      </Card>
    </div>
  );
}

interface ProvidersErrorProps {
  error: unknown;
  onRetry: () => void;
  retrying: boolean;
}

/** Figma `Providers/Page — error`: the health report failed; nothing else is drawn. */
export function ProvidersError({ error, onRetry, retrying }: ProvidersErrorProps) {
  return (
    <Card>
      <ErrorState
        compact
        error={error}
        onRetry={onRetry}
        retrying={retrying}
        title="Provider health didn’t load"
      />
    </Card>
  );
}

/** The next longer preset to offer from an empty window, or null from the longest. */
function longerPreset(range: ResolvedRange): RangePreset | null {
  if (range.value === "1h" || range.value === "24h") {
    return "7d";
  }
  return range.value === "30d" ? null : "30d";
}

const PRESET_NAMES: Record<RangePreset, string> = {
  "1h": "last hour",
  "24h": "last 24 hours",
  "7d": "last 7 days",
  "30d": "last 30 days",
};

/** Figma `Providers/Empty`: no LLM calls in the window (the incidents card still follows). */
export function ProvidersEmpty({ range }: { range: ResolvedRange }) {
  const navigate = useNavigate();
  const { orgId, projectId } = useProjectParams();
  const longer = longerPreset(range);

  return (
    <Card>
      <EmptyState
        icon={Activity}
        title="No LLM calls in this window"
        description="Provider health fills in when your app sends LLM calls through the SDK, the gateway or OTLP. Try a longer window, or check the environment filter."
        action={
          <>
            {longer ? (
              <Button
                variant="primary"
                onClick={() => {
                  void navigate({
                    to: ".",
                    search: (prev) => ({ ...prev, range: longer, from: undefined, to: undefined }),
                  });
                }}
              >
                Show {PRESET_NAMES[longer]}
              </Button>
            ) : null}
            <Button asChild>
              <Link to="/onboarding" search={{ org: orgId, project: projectId }}>
                Connect your app
              </Link>
            </Button>
          </>
        }
      />
    </Card>
  );
}
