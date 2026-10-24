import type { ReactNode } from "react";

import { PageHeader } from "@/components/page-header";

import { GatewayBaseUrl } from "./gateway-base-url";

interface GatewayLayoutProps {
  children: ReactNode;
}

/**
 * The frame every gateway page renders inside (Figma "Gateway/Page header"): the shared title, the
 * Base URL pill, then the page's own content. The shell's hub row is the sub-navigation. Pages put
 * their actions inside their content, not in the header.
 */
export function GatewayLayout({ children }: GatewayLayoutProps) {
  return (
    <div className="flex flex-col gap-6">
      <PageHeader
        title="Gateway"
        description="One endpoint for your OpenAI and Anthropic calls, with keys, limits, caching and fallbacks."
        actions={<GatewayBaseUrl />}
      />
      <div className="min-w-0">{children}</div>
    </div>
  );
}
