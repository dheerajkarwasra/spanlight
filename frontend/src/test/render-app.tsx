import { RouterProvider } from "@tanstack/react-router";
import { render } from "@testing-library/react";
import { vi } from "vitest";

import { Providers } from "@/app/providers";
import { createQueryClient } from "@/app/query-client";
import { createAppRouter } from "@/app/router";
import { ApiError, authApi, orgsApi, projectsApi, type Me, type Project } from "@/lib/api";

export const ORG_ID = "org_1";

/** The signed-in owner of `ORG_ID` that `mockSignedIn` answers `/me` with. */
export const ME: Me = {
  user: { id: "u1", email: "me@example.com", name: "Me", created_at: "", email_verified: true },
  memberships: [
    {
      org: { id: ORG_ID, name: "Acme", slug: "acme", is_demo: false, require_2fa: false },
      role: "owner",
    },
  ],
  has_password: true,
  totp_enabled: false,
  email_verification_required: false,
};

export function makeProject(id: string, name: string): Project {
  return {
    id,
    org_id: ORG_ID,
    name,
    slug: name.toLowerCase(),
    retention_days: 30,
    capture_payloads: true,
    weekly_digest_enabled: true,
    insight_channel_ids: [],
    created_at: "2026-10-01T00:00:00Z",
  };
}

export function mockSignedOut() {
  vi.spyOn(authApi, "me").mockRejectedValue(new ApiError(401, {}));
}

/** An owner of `ORG_ID`, which holds `projects`. */
export function mockSignedIn(projects: Project[]) {
  vi.spyOn(authApi, "me").mockResolvedValue(ME);
  vi.spyOn(orgsApi, "projects").mockResolvedValue(projects);
  vi.spyOn(projectsApi, "get").mockImplementation((projectId) => {
    const project = projects.find((candidate) => candidate.id === projectId);
    return project ? Promise.resolve(project) : Promise.reject(new ApiError(404, {}));
  });
}

/** The whole app (providers, route tree and guards) in the browser history, starting at `path`. */
export function renderApp(path: string) {
  window.history.replaceState(null, "", path);
  const queryClient = createQueryClient();
  const router = createAppRouter(queryClient);
  render(
    <Providers queryClient={queryClient}>
      <RouterProvider router={router} />
    </Providers>,
  );
  return router;
}
