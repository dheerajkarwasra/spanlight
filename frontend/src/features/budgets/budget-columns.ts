/**
 * Figma "Budgets — List": from the container width where the list turns into columns (64 rem), the
 * header and every row share one grid template, so the overline labels line up with the cells.
 * Scope, period, spend and resets keep the frame's widths; the actions column is fixed at what
 * Show burn, Edit and More need, so they never leave the card; the name column takes what is
 * left and its text truncates. Spelled out in full: Tailwind only generates classes it can read
 * verbatim in the source.
 */
export const BUDGET_GRID =
  "@[64rem]:grid @[64rem]:grid-cols-[minmax(0,1fr)_150px_70px_240px_150px_220px] @[64rem]:items-center";
