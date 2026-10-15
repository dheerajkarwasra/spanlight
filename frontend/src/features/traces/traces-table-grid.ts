import { cn } from "@/lib/utils";

/*
 * Figma "Traces/Table row": a 56px rounded row inside an 8px-padded card. Columns appear as the
 * card (a size container) gets wider, so the table never scrolls sideways:
 *   base      status · name · started · duration · models · cost
 *   @3xl      + tokens
 *   @4xl      + user / session
 *   65rem     + scores · tags (the full Figma layout at 1440px)
 * Hidden cells don't take a grid slot, so each template lists only the visible columns.
 */
export const ROW_GRID = cn(
  // 16px between columns, so a right-aligned header (Duration, Cost) does not run into the
  // left-aligned one after it.
  "grid items-center gap-4 px-3",
  "grid-cols-[20px_16px_minmax(0,1fr)_104px_64px_156px_88px]",
  "@3xl:grid-cols-[20px_16px_minmax(0,1fr)_104px_64px_156px_88px_88px]",
  "@4xl:grid-cols-[20px_16px_minmax(0,1fr)_104px_64px_156px_88px_88px_110px]",
  "@min-[65rem]:grid-cols-[20px_16px_minmax(0,1fr)_104px_64px_156px_88px_88px_100px_112px_110px]",
);

export const SHOW_TOKENS = "hidden @3xl:block";
export const SHOW_USER = "hidden @4xl:block";
export const SHOW_SCORES_AND_TAGS = "hidden @min-[65rem]:block";
