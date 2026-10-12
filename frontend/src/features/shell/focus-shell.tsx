import { Outlet } from "@tanstack/react-router";

import { NotFoundPage } from "@/app/route-pages";
import { isApiError } from "@/lib/api";

import { useProjectQuery } from "./project-context";
import { TwoFactorRequired } from "./two-factor-required";
import { useTwoFactorGate } from "./two-factor-gate";

/**
 * The project frame without chrome: the same not-found and two-factor gates as the full layout,
 * then the page, which draws its own frame.
 */
export function FocusShell() {
  const projectQuery = useProjectQuery();
  const twoFactorBlocked = useTwoFactorGate(projectQuery);
  if (projectQuery.isError && isApiError(projectQuery.error) && projectQuery.error.isNotFound) {
    return <NotFoundPage />;
  }
  if (twoFactorBlocked) {
    return (
      <main id="main" className="flex min-h-dvh flex-col bg-background px-4 py-10 lg:px-10">
        <TwoFactorRequired />
      </main>
    );
  }
  return <Outlet />;
}
