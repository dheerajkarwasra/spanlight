import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  createMemoryHistory,
  createRootRoute,
  createRouter,
  RouterProvider,
} from "@tanstack/react-router";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";

import { TooltipProvider } from "@/components/ui/tooltip";
import { ApiError, projectsApi, queryKeys } from "@/lib/api";
import { makeProject, ME } from "@/test/render-app";

import { ProjectDangerZone } from "./project-danger-zone";

it("keeps the project slug and shows a refused password on its field", async () => {
  const user = userEvent.setup();
  const project = makeProject("proj_1", "Checkout");
  const remove = vi
    .spyOn(projectsApi, "delete")
    .mockRejectedValue(
      new ApiError(422, { code: "INVALID_PASSWORD", detail: "That password is not correct." }),
    );
  const queryClient = new QueryClient();
  queryClient.setQueryData(queryKeys.me, ME);
  const router = createRouter({
    routeTree: createRootRoute({ component: () => <ProjectDangerZone project={project} /> }),
    history: createMemoryHistory({ initialEntries: ["/"] }),
  });
  render(
    <QueryClientProvider client={queryClient}>
      <TooltipProvider>
        <RouterProvider router={router} />
      </TooltipProvider>
    </QueryClientProvider>,
  );
  await user.click(await screen.findByRole("button", { name: "Delete project" }));
  const dialog = await screen.findByRole("dialog", { name: "Delete project" });
  const slug = within(dialog).getByLabelText("Type checkout to confirm");
  const password = within(dialog).getByLabelText("Password");
  await waitFor(() => expect(slug).toHaveFocus());
  await user.type(slug, "checkout");
  await user.type(password, "wrong password");
  await user.click(within(dialog).getByRole("button", { name: "Delete project" }));

  await waitFor(() => {
    expect(password).toBeInvalid();
    expect(password).toHaveAccessibleDescription("That password is not correct.");
    expect(password).toHaveFocus();
  });
  expect(remove).toHaveBeenCalledExactlyOnceWith("proj_1", "checkout", {
    password: "wrong password",
  });
  expect(slug).toHaveValue("checkout");
  expect(dialog).toBeVisible();
  expect(within(dialog).getByText("If email is set up, the owners are told.")).toBeVisible();
});
