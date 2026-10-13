import { useTheme } from "@/lib/theme";

import { shotSources, type ShotId, type ShotSources } from "./landing-shots";

/** Every captured screenshot, keyed by file name ("hero-light@1x.webp" → hashed asset URL). */
const SHOT_URLS: Record<string, string> = Object.fromEntries(
  Object.entries(
    import.meta.glob<string>("../../assets/landing/*.webp", {
      eager: true,
      query: "?url",
      import: "default",
    }),
  ).map(([path, url]) => [path.slice(path.lastIndexOf("/") + 1), url]),
);

/** The shot's image sources in the site's current theme; null until it has been captured. */
export function useShotSources(shot: ShotId): ShotSources | null {
  const { resolvedTheme } = useTheme();
  return shotSources(shot, resolvedTheme, SHOT_URLS);
}
