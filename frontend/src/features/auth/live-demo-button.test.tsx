import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { LiveDemoButton } from "./live-demo-button";

const mutate = vi.fn();
let isDemo = false;

vi.mock("@/features/shell", () => ({
  useCurrentOrg: () => ({ id: "org_1", is_demo: isDemo }),
}));

vi.mock("./queries", () => ({
  useMe: () => ({ user: { email: "me@example.com" } }),
}));

vi.mock("./use-demo-session", () => ({
  useDemoSession: () => ({ mutate, isPending: false }),
}));

beforeEach(() => {
  mutate.mockReset();
  isDemo = false;
});

describe("LiveDemoButton", () => {
  it("asks first, naming the account it signs out, then opens the demo once", async () => {
    const user = userEvent.setup();
    render(<LiveDemoButton />);

    await user.click(screen.getByRole("button", { name: "See it in the live demo" }));
    expect(screen.getByRole("alertdialog", { name: "Open the live demo?" })).toHaveTextContent(
      "Opening it signs you out of me@example.com",
    );
    expect(mutate).not.toHaveBeenCalled();

    await user.click(screen.getByRole("button", { name: "Open the demo" }));
    expect(mutate).toHaveBeenCalledTimes(1);
  });

  it("closes without opening the demo on Stay here", async () => {
    const user = userEvent.setup();
    render(<LiveDemoButton />);
    await user.click(screen.getByRole("button", { name: "See it in the live demo" }));
    await user.click(screen.getByRole("button", { name: "Stay here" }));
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
    expect(mutate).not.toHaveBeenCalled();
  });

  it("renders nothing inside the demo org", () => {
    isDemo = true;
    const { container } = render(<LiveDemoButton />);
    expect(container).toBeEmptyDOMElement();
  });
});
