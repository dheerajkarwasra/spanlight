import type { ReactNode } from "react";

import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { UnknownValue } from "@/components/unknown-value";
import { TruncatedBadge } from "@/features/traces/truncated-badge";
import { useMediaQuery } from "@/lib/use-media-query";
import { cn } from "@/lib/utils";

const NO_RECORDED_REASON = "The source span recorded no output text.";
const NO_REPLAY_REASON = "The provider answered without text.";

interface ReplayOutputPanesProps {
  recorded: string | null;
  replayed: string | null;
  /** The source span was cut at ingest; with a whole request that means its output was. */
  recordedTruncated: boolean;
}

function Pane({
  label,
  tone,
  badge,
  children,
}: {
  label: string;
  tone: "muted" | "accent";
  badge?: ReactNode;
  children: ReactNode;
}) {
  return (
    <section
      aria-label={label}
      className={cn(
        "flex min-w-0 flex-col gap-2 rounded-tile p-4",
        tone === "muted" ? "bg-surface-muted" : "bg-accent-subtle",
      )}
    >
      <div className="flex items-center gap-2">
        <h3 className="text-overline text-subtle-foreground uppercase">{label}</h3>
        {badge}
      </div>
      <div className="text-label leading-5 [overflow-wrap:anywhere] whitespace-pre-wrap text-foreground">
        {children}
      </div>
    </section>
  );
}

/** "Recorded output" beside "Replay output"; tabs below 640 px, where two columns don't fit. */
export function ReplayOutputPanes({
  recorded,
  replayed,
  recordedTruncated,
}: ReplayOutputPanesProps) {
  const wide = useMediaQuery("(min-width: 640px)");
  const recordedPane = (
    <Pane
      label="Recorded output"
      tone="muted"
      badge={recordedTruncated ? <TruncatedBadge /> : null}
    >
      {recorded ?? <UnknownValue reason={NO_RECORDED_REASON} />}
    </Pane>
  );
  const replayPane = (
    <Pane label="Replay output" tone="accent">
      {replayed ?? <UnknownValue reason={NO_REPLAY_REASON} />}
    </Pane>
  );
  if (wide) {
    return (
      <div className="grid grid-cols-2 gap-3">
        {recordedPane}
        {replayPane}
      </div>
    );
  }
  return (
    <Tabs defaultValue="replay" className="flex flex-col gap-3">
      <TabsList aria-label="Outputs" className="self-start">
        <TabsTrigger value="recorded">Recorded</TabsTrigger>
        <TabsTrigger value="replay">Replay</TabsTrigger>
      </TabsList>
      <TabsContent value="recorded">{recordedPane}</TabsContent>
      <TabsContent value="replay">{replayPane}</TabsContent>
    </Tabs>
  );
}
