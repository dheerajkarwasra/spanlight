import { Badge } from "@/components/ui/badge";
import type { ReviewReason } from "@/lib/api";

import { reasonLabel } from "./reason-copy";
import { REASON_STYLES } from "./reason-style";

interface ReasonChipProps {
  reason: ReviewReason;
  size?: "sm" | "md";
}

/** Figma "Reason chip": why an item is in its queue, as an icon and a word. */
export function ReasonChip({ reason, size = "sm" }: ReasonChipProps) {
  const { icon: Icon, tone } = REASON_STYLES[reason];
  return (
    <Badge variant={tone} size={size}>
      <Icon aria-hidden />
      {reasonLabel(reason)}
    </Badge>
  );
}
