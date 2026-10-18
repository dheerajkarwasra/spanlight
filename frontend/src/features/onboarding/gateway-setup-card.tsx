import { Link } from "@tanstack/react-router";
import { Waypoints } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";

import { INK_FOCUS } from "./api-key-styles";

interface GatewaySetupCardProps {
  orgId: string;
  projectId: string;
}

/**
 * Replaces the ink API key card on the "No SDK" tab (Figma "Gateway setup card"). The gateway
 * authenticates with its own keys, so the ingest key must not be offered next to its snippets.
 */
export function GatewaySetupCard({ orgId, projectId }: GatewaySetupCardProps) {
  return (
    <Card variant="hero" className="flex flex-col gap-3.5 overflow-hidden p-5 sm:p-6">
      <div className="relative flex min-w-0 items-center gap-2.5">
        <span
          aria-hidden
          className="flex size-8 shrink-0 items-center justify-center rounded-[10px] bg-lime text-lime-foreground"
        >
          <Waypoints className="size-4" strokeWidth={2} />
        </span>
        <h2 className="text-sm font-semibold text-rail-foreground">Use the gateway</h2>
      </div>

      <p className="relative text-sm text-rail-muted-foreground">
        Add a provider credential, then create a gateway key.
      </p>

      <div className="relative">
        <Button asChild variant="highlight" size="lg" className={`w-full sm:w-auto ${INK_FOCUS}`}>
          <Link to="/$orgId/$projectId/gateway/credentials" params={{ orgId, projectId }}>
            Add a provider credential
          </Link>
        </Button>
      </div>
    </Card>
  );
}
