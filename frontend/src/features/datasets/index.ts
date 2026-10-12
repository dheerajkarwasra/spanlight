/**
 * What other features may use from `features/datasets`: the "Add to dataset" and "Add to queue"
 * dialogs the traces screens open. The router loads the pages from their own modules, so they
 * stay out of the traces bundle.
 */
export { AddToDatasetDialog, type AddTarget } from "./add-to-dataset-dialog";
export { AddToQueueDialog } from "./add-to-queue-dialog";
export type { TraceRef } from "./span-pick";
