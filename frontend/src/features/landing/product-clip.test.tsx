import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ProductClip } from "./product-clip";

function preferReducedMotion(reduce: boolean) {
  vi.spyOn(window, "matchMedia").mockImplementation(
    (query: string) =>
      ({
        matches: reduce && query === "(prefers-reduced-motion: reduce)",
        media: query,
        addEventListener: () => undefined,
        removeEventListener: () => undefined,
      }) as unknown as MediaQueryList,
  );
}

describe("ProductClip", () => {
  let play: ReturnType<typeof vi.spyOn>;
  let pause: ReturnType<typeof vi.spyOn>;

  beforeEach(() => {
    // jsdom does not implement media playback.
    play = vi.spyOn(HTMLMediaElement.prototype, "play").mockResolvedValue(undefined);
    pause = vi.spyOn(HTMLMediaElement.prototype, "pause").mockImplementation(() => undefined);
  });

  it("starts the muted clip on its own by default", () => {
    preferReducedMotion(false);
    render(<ProductClip />);

    expect(play).toHaveBeenCalledOnce();
    const video = screen.getByLabelText<HTMLVideoElement>(/repeated tool call/);
    expect(video.muted).toBe(true);
  });

  it("keeps the poster and offers a play button under reduced motion", () => {
    preferReducedMotion(true);
    render(<ProductClip />);

    expect(play).not.toHaveBeenCalled();
    expect(pause).toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "Play product clip" })).toBeVisible();
  });
});
