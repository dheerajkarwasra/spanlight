import { Outlet, useLocation } from "@tanstack/react-router";

import { NoAccess } from "@/components/no-access";
import { PageHeader } from "@/components/page-header";
import { useCurrentRole } from "@/features/shell";
import { can } from "@/lib/permissions";

import { SettingsNav } from "./settings-nav";
import { activeSettingsItem, SETTINGS_NAV_GROUPS } from "./settings-nav-items";

export function SettingsLayout() {
  const role = useCurrentRole();
  const pathname = useLocation({ select: (location) => location.pathname });
  // The nav hides a page the role can't read; a direct link gets the no-access state instead.
  const item = activeSettingsItem(pathname, SETTINGS_NAV_GROUPS);
  const permission = item?.permission;
  const blocked = role !== null && permission !== undefined && !can(role, permission);

  return (
    <div className="flex flex-col gap-6">
      <PageHeader
        title="Settings"
        description="Manage this project, your organization's members and your account."
      />
      <div className="flex flex-col gap-6 md:flex-row md:items-start">
        <SettingsNav />
        <div className="min-w-0 flex-1">
          {blocked && item ? <NoAccess page={item.label} permission={permission} /> : <Outlet />}
        </div>
      </div>
    </div>
  );
}
