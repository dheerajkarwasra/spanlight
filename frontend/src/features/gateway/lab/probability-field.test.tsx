import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { ProbabilityField } from "./probability-field";

describe("ProbabilityField", () => {
  it("names the slider thumb, the element that carries the slider role", () => {
    // Finding T9-01: the name sat on the slider root, which has no role.
    render(<ProbabilityField value="25" onChange={() => undefined} />);

    expect(screen.getByRole("slider", { name: "Probability in percent" })).toBeInTheDocument();
  });
});
