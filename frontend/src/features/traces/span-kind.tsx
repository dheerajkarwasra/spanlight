import { Badge } from "@/components/ui/badge";
import type { SpanKind } from "@/lib/api";
import { cn } from "@/lib/utils";

import { kindMeta } from "./span-kind-meta";

/** Figma "Traces/Kind icon": the kind's lucide glyph tinted with its kind colour. */
export function SpanKindIcon({ kind, className }: { kind: SpanKind; className?: string }) {
  const meta = kindMeta(kind);
  const Icon = meta.icon;
  return <Icon aria-hidden className={cn("size-4 shrink-0", meta.textClass, className)} />;
}

/** Kind label in the span header; LLM spans get the violet accent. */
export function SpanKindBadge({ kind }: { kind: SpanKind }) {
  return <Badge variant={kind === "llm" ? "accent" : "neutral"}>{kindMeta(kind).label}</Badge>;
}
