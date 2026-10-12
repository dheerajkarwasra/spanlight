/** Figma "Evaluators — Detail" run columns, from the container width where columns appear. */
export const RUN_COLUMNS = {
  trace: "@[40rem]:w-[120px] @[40rem]:shrink-0",
  status: "@[40rem]:w-[110px] @[40rem]:shrink-0",
  result: "min-w-0 flex-1",
  cost: "@[40rem]:w-[80px] @[40rem]:shrink-0 @[40rem]:text-right",
} as const;
