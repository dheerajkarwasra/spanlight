import { Ban, SquareArrowOutUpRight } from "lucide-react";

import { CopyButton } from "@/components/copy-button";
import { useMe } from "@/features/auth";

import { gatewayBaseUrl } from "./gateway-url";

const PILL_CLASS =
  "flex max-w-full items-center gap-2.5 rounded-full border border-border bg-surface py-1.5 shadow-card";

/**
 * The Base URL pill of the page header (Figma "Gateway/Page header", G1). Embedded: this origin's
 * gateway URL with Copy. A standalone gateway runs at its own address and a disabled one at none,
 * so neither offers a URL to copy; each says why instead.
 */
export function GatewayBaseUrl() {
  const mode = useMe().capabilities.gateway_mode;

  if (mode === "embedded") {
    const url = gatewayBaseUrl();
    return (
      <div className={`${PILL_CLASS} pr-1.5 pl-4`}>
        <span className="text-xs font-medium text-subtle-foreground">Base URL</span>
        <code className="min-w-0 truncate font-mono text-[13px] text-foreground">{url}</code>
        <CopyButton value={url} label="Copy" showLabel className="shrink-0 bg-surface-muted" />
      </div>
    );
  }

  return (
    <div className={`${PILL_CLASS} min-h-11 px-4`}>
      <span className="shrink-0 text-xs font-medium text-subtle-foreground">Base URL</span>
      {mode === "standalone" ? (
        <span className="flex min-w-0 items-center gap-1.5 text-[13px] font-medium text-foreground">
          <SquareArrowOutUpRight aria-hidden className="size-3.5 shrink-0" />
          <span>Standalone gateway · use its own URL</span>
        </span>
      ) : (
        <span className="flex min-w-0 items-center gap-1.5 text-[13px] font-medium text-muted-foreground">
          <Ban aria-hidden className="size-3.5 shrink-0" />
          <span>
            Gateway off on this server · set <code className="font-mono">GATEWAY_MODE</code>
          </span>
        </span>
      )}
    </div>
  );
}
