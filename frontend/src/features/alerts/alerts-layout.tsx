import { Link } from "@tanstack/react-router";
import type { ReactNode } from "react";

import { PageHeader } from "@/components/page-header";
import { useProjectParams } from "@/features/shell";

import { FiringBanner } from "./firing-banner";

/**
 * The frame the alert rules render inside (Figma "Alerts/Page header"): the shared title and
 * description, the firing banner, then the rules card with `aside` (the firing history) to its
 * right at 1280 px and wider, under it below. The card's primary action sits in its header, not here.
 * Channels live under Settings, so the description links there.
 */
export function AlertsLayout({ children, aside }: { children: ReactNode; aside?: ReactNode }) {
  const params = useProjectParams();
  return (
    <div className="flex flex-col gap-6">
      <PageHeader
        title="Alerts"
        description={
          <>
            Rules watch your metrics and tell you when something is wrong. Where they send is set in{" "}
            <Link
              to="/$orgId/$projectId/settings/channels"
              params={params}
              className="rounded-xs font-semibold text-accent underline-offset-4 hover:underline"
            >
              Settings › Notification channels
            </Link>
            .
          </>
        }
      />
      <FiringBanner />
      <div className="grid gap-6 xl:grid-cols-12">
        <div className="min-w-0 xl:col-span-8">{children}</div>
        {aside ? <div className="min-w-0 xl:col-span-4">{aside}</div> : null}
      </div>
    </div>
  );
}
