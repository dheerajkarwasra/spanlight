import { Scissors } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Tooltip } from "@/components/ui/tooltip";

import { PAYLOAD_LIMIT_BYTES } from "./payload-format";

const TRUNCATED_REASON = `Payload exceeded ${PAYLOAD_LIMIT_BYTES / (1024 * 1024)} MB and was cut`;

/** Figma "Traces/Truncated badge": amber pill with a scissors glyph and an explanation. */
export function TruncatedBadge() {
  return (
    <Tooltip content={TRUNCATED_REASON}>
      <Badge
        variant="warning"
        tabIndex={0}
        className="cursor-help gap-1 px-2 py-0.5 font-medium [&_svg]:size-3"
      >
        <Scissors aria-hidden />
        Truncated
        <span className="sr-only">: {TRUNCATED_REASON}</span>
      </Badge>
    </Tooltip>
  );
}
