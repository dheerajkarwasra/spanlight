import {
  Bot,
  Boxes,
  Circle,
  Database,
  Globe,
  Sparkles,
  Wrench,
  type LucideIcon,
} from "lucide-react";

import type { SpanKind } from "@/lib/api";

interface KindMeta {
  label: string;
  icon: LucideIcon;
  /** Waterfall bar fill. */
  barClass: string;
  /** Icon tint. */
  textClass: string;
}

export const SPAN_KIND_META: Record<SpanKind, KindMeta> = {
  llm: { label: "LLM", icon: Sparkles, barClass: "bg-kind-llm", textClass: "text-kind-llm" },
  tool: { label: "Tool", icon: Wrench, barClass: "bg-kind-tool", textClass: "text-kind-tool" },
  retrieval: {
    label: "Retrieval",
    icon: Database,
    barClass: "bg-kind-retrieval",
    textClass: "text-kind-retrieval",
  },
  chain: { label: "Chain", icon: Boxes, barClass: "bg-kind-chain", textClass: "text-kind-chain" },
  agent: { label: "Agent", icon: Bot, barClass: "bg-kind-agent", textClass: "text-kind-agent" },
  http: { label: "HTTP", icon: Globe, barClass: "bg-kind-http", textClass: "text-kind-http" },
  other: { label: "Other", icon: Circle, barClass: "bg-kind-other", textClass: "text-kind-other" },
};

/** A kind's meta; `other` for a kind this bundle does not know (an older tab on a newer API). */
export function kindMeta(kind: SpanKind): KindMeta {
  return (SPAN_KIND_META as Partial<Record<string, KindMeta>>)[kind] ?? SPAN_KIND_META.other;
}
