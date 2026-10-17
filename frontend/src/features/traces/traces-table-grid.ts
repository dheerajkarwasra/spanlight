import { cn } from "@/lib/utils";

/*
 * Figma "Traces/Table row": a 56px rounded row inside an 8px-padded card. Columns appear as the
 * card (a size container) gets wider, so the table never scrolls sideways. Hidden cells don't
 * take a grid slot, so each template lists only the visible columns. The smallest card is about
 * 650px wide (a 1024px window beside the sidebar); the name column takes what is left.
 *
 * Default (Figma "Traces/List/Retry cost column"):
 *   base      status · name · started · duration · models · cost
 *   @3xl      + tokens
 *   54rem     + retries
 *   @5xl      + user / session
 *   74rem     + scores · tags
 *
 * While the retry-cost sort or the has-retries filter is on, a Retry cost column follows Retries
 * (the default list keeps its width). Both retry columns show from the smallest card, so the models column waits:
 *   base      status · name · started · duration · cost · retries · retry cost
 *   @3xl      + tokens
 *   @5xl      + models
 *   @6xl      + user / session
 *   84rem     + scores · tags
 *
 * When the project promotes metadata keys (Figma "Traces / Metadata chips and filter"), a Metadata
 * column follows Name and takes a share of the free width, ahead of tokens and retries:
 *   base      status · name · started · duration · models · cost
 *   54rem     + metadata
 *   @5xl      + tokens
 *   74rem     + retries
 *   84rem     + user / session
 *   96rem     + scores · tags
 * The retry-cost layout has no metadata column: its two retry columns already take that width.
 * shortcut: a row shows one metadata chip and "+N"; measure the cell if wide cards should show more.
 */

export interface TableLayout {
  /** The row's grid template at each width. */
  row: string;
  /** Visibility classes per optional column; "" is always shown. */
  models: string;
  tokens: string;
  retries: string;
  user: string;
  scoresAndTags: string;
  /** Whether the Retry cost column exists at all. */
  retryCost: boolean;
  /** The Metadata column's visibility classes, or null when the layout has no such column. */
  metadata: string | null;
}

// 16px between columns, so a right-aligned header (Duration, Cost) does not run into the
// left-aligned one after it.
const ROW_BASE = "grid items-center gap-4 px-3";

export const DEFAULT_LAYOUT: TableLayout = {
  row: cn(
    ROW_BASE,
    "grid-cols-[20px_16px_minmax(0,1fr)_104px_64px_156px_88px]",
    "@3xl:grid-cols-[20px_16px_minmax(0,1fr)_104px_64px_156px_88px_88px]",
    "@min-[54rem]:grid-cols-[20px_16px_minmax(0,1fr)_104px_64px_156px_88px_88px_56px]",
    "@5xl:grid-cols-[20px_16px_minmax(0,1fr)_104px_64px_156px_88px_88px_56px_110px]",
    "@min-[74rem]:grid-cols-[20px_16px_minmax(0,1fr)_104px_64px_156px_88px_88px_56px_100px_112px_110px]",
  ),
  models: "",
  tokens: "hidden @3xl:block",
  retries: "hidden @min-[54rem]:block",
  user: "hidden @5xl:block",
  scoresAndTags: "hidden @min-[74rem]:block",
  retryCost: false,
  metadata: null,
};

const METADATA_LAYOUT: TableLayout = {
  row: cn(
    ROW_BASE,
    "grid-cols-[20px_16px_minmax(0,1fr)_104px_64px_156px_88px]",
    "@min-[54rem]:grid-cols-[20px_16px_minmax(0,1fr)_minmax(0,1.25fr)_104px_64px_156px_88px]",
    "@5xl:grid-cols-[20px_16px_minmax(0,1fr)_minmax(0,1.25fr)_104px_64px_156px_88px_88px]",
    "@min-[74rem]:grid-cols-[20px_16px_minmax(0,1fr)_minmax(0,1.25fr)_104px_64px_156px_88px_88px_56px]",
    "@min-[84rem]:grid-cols-[20px_16px_minmax(0,1fr)_minmax(0,1.25fr)_104px_64px_156px_88px_88px_56px_110px]",
    "@min-[96rem]:grid-cols-[20px_16px_minmax(0,1fr)_minmax(0,1.25fr)_104px_64px_156px_88px_88px_56px_100px_112px_110px]",
  ),
  models: "",
  tokens: "hidden @5xl:block",
  retries: "hidden @min-[74rem]:block",
  user: "hidden @min-[84rem]:block",
  scoresAndTags: "hidden @min-[96rem]:block",
  retryCost: false,
  metadata: "hidden @min-[54rem]:block",
};

export const RETRY_COST_LAYOUT: TableLayout = {
  row: cn(
    ROW_BASE,
    "grid-cols-[20px_16px_minmax(0,1fr)_104px_64px_88px_56px_88px]",
    "@3xl:grid-cols-[20px_16px_minmax(0,1fr)_104px_64px_88px_88px_56px_88px]",
    "@5xl:grid-cols-[20px_16px_minmax(0,1fr)_104px_64px_156px_88px_88px_56px_88px]",
    "@6xl:grid-cols-[20px_16px_minmax(0,1fr)_104px_64px_156px_88px_88px_56px_88px_110px]",
    "@min-[84rem]:grid-cols-[20px_16px_minmax(0,1fr)_104px_64px_156px_88px_88px_56px_88px_100px_112px_110px]",
  ),
  models: "hidden @5xl:block",
  tokens: "hidden @3xl:block",
  retries: "",
  user: "hidden @6xl:block",
  scoresAndTags: "hidden @min-[84rem]:block",
  retryCost: true,
  metadata: null,
};

export function tableLayout(showRetryCost: boolean, showMetadata = false): TableLayout {
  if (showRetryCost) {
    return RETRY_COST_LAYOUT;
  }
  return showMetadata ? METADATA_LAYOUT : DEFAULT_LAYOUT;
}
