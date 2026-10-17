import { CircleAlert, Coins, Hand, Shuffle, Stethoscope, type LucideIcon } from "lucide-react";

import type { ReviewReason } from "@/lib/api";

interface ReasonStyle {
  icon: LucideIcon;
  /** The `Badge` tone of the reason's chip. */
  tone: "accent" | "danger" | "warning" | "outline";
  /** The annotate banner's background and its icon's colour. */
  banner: string;
  iconColor: string;
}

/** How each reason looks: an icon and a tone, so a chip never relies on colour alone. */
export const REASON_STYLES: Record<ReviewReason, ReasonStyle> = {
  doctor_finding: {
    icon: Stethoscope,
    tone: "accent",
    banner: "bg-accent-subtle",
    iconColor: "text-accent",
  },
  error: {
    icon: CircleAlert,
    tone: "danger",
    banner: "bg-danger-subtle",
    iconColor: "text-danger-text",
  },
  cost_outlier: {
    icon: Coins,
    tone: "warning",
    banner: "bg-warning-subtle",
    iconColor: "text-warning",
  },
  random: {
    icon: Shuffle,
    tone: "outline",
    banner: "bg-surface-muted",
    iconColor: "text-muted-foreground",
  },
  manual: {
    icon: Hand,
    tone: "outline",
    banner: "bg-surface-muted",
    iconColor: "text-muted-foreground",
  },
};
