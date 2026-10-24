import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { OrganizationPage } from "./organization-page";

vi.mock("@/features/shell", () => ({
  useCurrentOrg: () => ({ id: "org_1", is_demo: false }),
  usePermission: () => true,
}));
vi.mock("./organization-form", () => ({
  OrganizationForm: () => <button type="submit">Save changes</button>,
}));
vi.mock("./org-security-card", () => ({ OrgSecurityCard: () => <p>Security card</p> }));
vi.mock("./danger-zone", () => ({ OrgDangerZone: () => null }));

describe("OrganizationPage", () => {
  it("keeps the form at 720 px and puts the self-saving security card under its own heading", () => {
    const { container } = render(<OrganizationPage />);

    expect(container.firstElementChild).toHaveClass("max-w-[720px]");
    const heading = screen.getByRole("heading", { level: 2, name: "Saved as you change them" });
    const save = screen.getByRole("button", { name: "Save changes" });
    const card = screen.getByText("Security card");
    expect(save.compareDocumentPosition(heading) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(heading.compareDocumentPosition(card) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });
});
