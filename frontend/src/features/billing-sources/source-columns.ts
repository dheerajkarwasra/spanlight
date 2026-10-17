/**
 * Column classes shared by the list's labels and rows so they line up (Figma "Bet1/Source row":
 * Status 190 px, Projects 88 px, Actions 204 px right-aligned). From 40rem of card width they sit
 * in columns; below that the row stacks into the mobile tile (Figma "Billing sources / mobile
 * 375"), with 48 px action buttons in a row of their own.
 */
export const SOURCE_COLUMNS = {
  name: "min-w-0 flex-1",
  status: "flex min-w-0 flex-col items-start gap-1 @[40rem]:w-[190px] @[40rem]:shrink-0",
  projects: "text-sm text-foreground @[40rem]:w-[88px] @[40rem]:shrink-0",
  actions: "flex items-center gap-2 @[40rem]:w-[204px] @[40rem]:shrink-0 @[40rem]:justify-end",
} as const;

/** A text action (Refresh, Upload): 40 px in the row, 48 px on the mobile tile. */
export const TEXT_ACTION_CLASS = "@max-[40rem]:h-12 @max-[40rem]:px-6";

/** An icon action (Edit, Delete): a 32 px outlined circle, 48 px on the mobile tile. */
export const ICON_ACTION_CLASS =
  "border-border-strong shadow-none @max-[40rem]:size-12 @max-[40rem]:[&_svg]:size-5";
