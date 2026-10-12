import { describe, expect, it } from "vitest";

import {
  SHOT_IDS,
  SHOT_SCALES,
  SHOT_THEMES,
  SHOTS,
  shotFileName,
  shotSources,
} from "./landing-shots";

/** File names of the screenshots captured so far. */
const CAPTURED = Object.keys(import.meta.glob("../../assets/landing/*.webp")).map((path) =>
  path.slice(path.lastIndexOf("/") + 1),
);

const EXPECTED = SHOT_IDS.flatMap((id) =>
  SHOT_THEMES.flatMap((theme) => SHOT_SCALES.map((scale) => shotFileName(id, theme, scale))),
);

describe("shotSources", () => {
  const urls = {
    "doctor-light@1x.webp": "/assets/doctor-light@1x-abc.webp",
    "doctor-light@2x.webp": "/assets/doctor-light@2x-def.webp",
    "doctor-dark@1x.webp": "/assets/doctor-dark@1x-123.webp",
  };

  it("pairs the 1x and 2x files as width descriptors", () => {
    expect(shotSources("doctor", "light", urls)).toEqual({
      src: "/assets/doctor-light@1x-abc.webp",
      srcSet: `/assets/doctor-light@1x-abc.webp ${SHOTS.doctor.width}w, /assets/doctor-light@2x-def.webp ${SHOTS.doctor.width * 2}w`,
    });
  });

  it("returns nothing unless both scales were captured", () => {
    expect(shotSources("doctor", "dark", urls)).toBeNull();
    expect(shotSources("hero", "light", urls)).toBeNull();
  });
});

describe("captured screenshots", () => {
  it("are a complete set once any exist: every shot, both themes, 1x and 2x", () => {
    if (CAPTURED.length > 0) {
      expect([...CAPTURED].sort()).toEqual([...EXPECTED].sort());
    }
  });

  it("each describe what they show", () => {
    for (const id of SHOT_IDS) {
      expect(SHOTS[id].alt.length).toBeGreaterThan(40);
    }
  });
});
