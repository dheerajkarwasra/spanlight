import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { Fact } from "./legal-parts";

describe("Fact", () => {
  it("links a confirmed fact to its page when one is given", () => {
    render(
      <Fact value="Example authority" what="supervisory authority" href="https://example.org/" />,
    );

    expect(screen.getByRole("link", { name: "Example authority" })).toHaveAttribute(
      "href",
      "https://example.org/",
    );
  });

  it("shows the plain fact without a page", () => {
    render(<Fact value="Berlin" what="federal state" />);

    expect(screen.getByText("Berlin")).toBeInTheDocument();
    expect(screen.queryByRole("link")).not.toBeInTheDocument();
  });
});
