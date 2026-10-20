/**
 * What other features may use from `features/datasets`: the "Add to dataset" and "Add to queue"
 * dialogs the traces screens open, and the dataset picker, duplicate warning and error copy
 * review work mode's "Save as dataset item" reuses. The router loads the pages from their own
 * modules, so they stay out of the traces bundle.
 */
export { AddToDatasetDialog, type AddTarget } from "./add-to-dataset-dialog";
export { AddToQueueDialog } from "./add-to-queue-dialog";
export { AlsoInNote } from "./also-in-note";
export { datasetErrorMessage, rejectionLabel } from "./dataset-errors";
export { useDatasetOption } from "./dataset-queries";
export { DatasetSelect } from "./dataset-select";
export type { TraceRef } from "./span-pick";
