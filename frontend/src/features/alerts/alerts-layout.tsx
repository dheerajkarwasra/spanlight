import type { ReactNode } from "react";

import { PageHeader } from "@/components/page-header";

import { AlertsHeaderTabs } from "./alerts-header-tabs";

/**
 * The frame the Rules and Channels tabs render inside (Figma "Alerts/Page header" and
 * "Alerts/Sub-nav"): the shared title and description, then the tabs, then the tab's card. The
 * tab's primary action sits in its card header, not here.
 */
export function AlertsLayout({ children }: { children: ReactNode }) {
  return (
    <div className="flex flex-col gap-6">
      <PageHeader
        title="Alerts"
        description="Rules watch your metrics and tell you when something is wrong. Channels decide who hears about it."
      />
      <AlertsHeaderTabs />
      <div className="min-w-0">{children}</div>
    </div>
  );
}
