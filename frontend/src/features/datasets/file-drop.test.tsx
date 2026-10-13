import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { FileDrop } from "./file-drop";

describe("FileDrop", () => {
  it("names its file input for assistive technology", () => {
    // The input is visually hidden but still focusable (finding T9-02).
    render(<FileDrop chosen={null} onChoose={() => undefined} onClear={() => undefined} />);

    const input = screen.getByLabelText("Choose a .jsonl or .csv file");
    expect(input).toHaveAttribute("type", "file");
  });
});
