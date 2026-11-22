import { getRouteApi } from "@tanstack/react-router";

import { PageHeader } from "@/components/page-header";

import { InsightsTab } from "./insights-tab";
import { LastCheckedPill } from "./last-checked-pill";
import { PrecisionTab } from "./precision-tab";
import { SensitivityTab } from "./sensitivity-tab";
import { StatusTabs, type DoctorTab } from "./status-tabs";

const doctorRoute = getRouteApi("/_authed/$orgId/$projectId/doctor");

/**
 * Figma "Doctor — Insights list", "Doctor/Precision" and "Doctor/Sensitivity": status tabs,
 * filters and the project's insights, how often each check was right, or how sensitive each is.
 */
export function DoctorPage() {
  const search = doctorRoute.useSearch();
  const navigate = doctorRoute.useNavigate();

  function setTab(tab: DoctorTab) {
    void navigate({
      search: (prev) =>
        tab === "precision" || tab === "sensitivity"
          ? { ...prev, tab }
          : { ...prev, tab: "insights" as const, status: tab },
    });
  }

  return (
    <div className="flex flex-col gap-6">
      <PageHeader
        title="Doctor"
        description="Every 15 minutes, the Doctor checks your traffic and opens a finding when it spots a problem."
        actions={<LastCheckedPill />}
      />
      <StatusTabs
        value={search.tab === "insights" ? search.status : search.tab}
        onChange={setTab}
      />
      {search.tab === "precision" ? (
        <PrecisionTab
          onOpenInsights={() => {
            setTab("open");
          }}
        />
      ) : search.tab === "sensitivity" ? (
        <SensitivityTab />
      ) : (
        <InsightsTab />
      )}
    </div>
  );
}
