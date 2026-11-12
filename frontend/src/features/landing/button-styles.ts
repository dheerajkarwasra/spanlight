import { cn } from "@/lib/utils";

export const BUTTON_BASE =
  "lp-control inline-flex min-h-11 items-center justify-center rounded-full border px-[22px] py-3 text-[15px] leading-[1.3] font-medium whitespace-nowrap disabled:cursor-progress";

/** White pill on a black section (Figma "Landing/Button/On-dark primary"); hover inverts it. */
export const BUTTON_PRIMARY = cn(
  BUTTON_BASE,
  "border-mono-white bg-mono-white text-mono-black hover:bg-mono-black hover:text-mono-white",
);

/** Outlined pill on a black section (Figma "Landing/Button/On-dark secondary"). */
export const BUTTON_SECONDARY = cn(
  BUTTON_BASE,
  "border-mono-500 text-mono-white hover:border-mono-white hover:bg-mono-white hover:text-mono-black",
);
