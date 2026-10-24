import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "@tanstack/react-router";
import { toast } from "sonner";

import { useProjectParams } from "@/features/shell";
import {
  annotationsApi,
  datasetsApi,
  queryKeys,
  type Dataset,
  type DatasetCreate,
  type DatasetItemInput,
  type DatasetItemUpdate,
  type DatasetUpdate,
} from "@/lib/api";

export { useScoreConfigsQuery } from "@/features/scores";

export const DATASET_PAGE_SIZE = 50;
export const ITEM_PAGE_SIZE = 50;
/** Enough for a picker: the API's largest page. */
const OPTION_LIMIT = 200;

/** The project's datasets, newest first, a page at a time. */
export function useDatasetListQuery() {
  const { projectId } = useProjectParams();
  return useInfiniteQuery({
    queryKey: queryKeys.project(projectId).datasets.list({ limit: DATASET_PAGE_SIZE }),
    queryFn: ({ pageParam }) =>
      datasetsApi.list(projectId, { limit: DATASET_PAGE_SIZE, cursor: pageParam }),
    initialPageParam: null as string | null,
    getNextPageParam: (lastPage) => lastPage.next_cursor,
  });
}

/** The datasets a picker offers (upload, add from traces): one page of the largest size. */
export function useDatasetOptionsQuery(enabled = true) {
  const { projectId } = useProjectParams();
  return useQuery({
    queryKey: [...queryKeys.project(projectId).datasets.all, "options"],
    queryFn: () => datasetsApi.list(projectId, { limit: OPTION_LIMIT }),
    enabled,
  });
}

/** The dataset with this id among the picker's options, once they are loaded. */
export function useDatasetOption(datasetId: string): Dataset | undefined {
  return useDatasetOptionsQuery().data?.items.find((dataset) => dataset.id === datasetId);
}

export function useDatasetQuery(datasetId: string) {
  const { projectId } = useProjectParams();
  return useQuery({
    queryKey: queryKeys.project(projectId).datasets.detail(datasetId),
    queryFn: () => datasetsApi.get(projectId, datasetId),
  });
}

/** A dataset's items, oldest first, a page at a time. */
export function useDatasetItemsQuery(datasetId: string) {
  const { projectId } = useProjectParams();
  return useInfiniteQuery({
    queryKey: queryKeys.project(projectId).datasets.items(datasetId, { limit: ITEM_PAGE_SIZE }),
    queryFn: ({ pageParam }) =>
      datasetsApi.items(projectId, datasetId, { limit: ITEM_PAGE_SIZE, cursor: pageParam }),
    initialPageParam: null as string | null,
    getNextPageParam: (lastPage) => lastPage.next_cursor,
  });
}

/** Every dataset query of the project: counts change with every write. */
function useInvalidateDatasets() {
  const { projectId } = useProjectParams();
  const queryClient = useQueryClient();
  return () =>
    queryClient.invalidateQueries({ queryKey: queryKeys.project(projectId).datasets.all });
}

export function useCreateDataset() {
  const { projectId } = useProjectParams();
  const invalidate = useInvalidateDatasets();
  return useMutation({
    mutationFn: (input: DatasetCreate) => datasetsApi.create(projectId, input),
    onSuccess: invalidate,
  });
}

/**
 * Deletes a dataset and leaves its page first: dropping the cache of a page that is still mounted
 * would refetch it and flash "Dataset not found".
 */
export function useDeleteDataset() {
  const { orgId, projectId } = useProjectParams();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (dataset: Dataset) => datasetsApi.delete(projectId, dataset.id),
    onSuccess: async (_result, dataset) => {
      toast.success(`Deleted dataset “${dataset.name}”.`);
      await navigate({ to: "/$orgId/$projectId/datasets", params: { orgId, projectId } });
      const keys = queryKeys.project(projectId).datasets;
      queryClient.removeQueries({ queryKey: keys.detail(dataset.id) });
      await queryClient.invalidateQueries({ queryKey: keys.all });
    },
  });
}

/** Edits the dataset's description (the only editable field); `null` clears it. */
export function useUpdateDataset(datasetId: string) {
  const { projectId } = useProjectParams();
  const invalidate = useInvalidateDatasets();
  return useMutation({
    mutationFn: (update: DatasetUpdate) => datasetsApi.update(projectId, datasetId, update),
    onSuccess: invalidate,
  });
}

/** Locks the dataset against item edits; needs `datasets:lock` (admin). */
export function useLockDataset(datasetId: string) {
  const { projectId } = useProjectParams();
  const invalidate = useInvalidateDatasets();
  return useMutation({
    mutationFn: () => datasetsApi.lock(projectId, datasetId),
    onSuccess: invalidate,
  });
}

/** Unlocks the dataset so items can be edited again; needs `datasets:lock` (admin). */
export function useUnlockDataset(datasetId: string) {
  const { projectId } = useProjectParams();
  const invalidate = useInvalidateDatasets();
  return useMutation({
    mutationFn: () => datasetsApi.unlock(projectId, datasetId),
    onSuccess: invalidate,
  });
}

export function useAddItems(datasetId: string) {
  const { projectId } = useProjectParams();
  const invalidate = useInvalidateDatasets();
  return useMutation({
    mutationFn: (items: DatasetItemInput[]) => datasetsApi.addItems(projectId, datasetId, items),
    onSuccess: invalidate,
  });
}

export function useUpdateItem(datasetId: string) {
  const { projectId } = useProjectParams();
  const invalidate = useInvalidateDatasets();
  return useMutation({
    mutationFn: ({ itemId, update }: { itemId: string; update: DatasetItemUpdate }) =>
      datasetsApi.updateItem(projectId, datasetId, itemId, update),
    onSuccess: invalidate,
  });
}

export function useDeleteItem(datasetId: string) {
  const { projectId } = useProjectParams();
  const invalidate = useInvalidateDatasets();
  return useMutation({
    mutationFn: (itemId: string) => datasetsApi.deleteItem(projectId, datasetId, itemId),
    onSuccess: invalidate,
  });
}

export function useUploadItems() {
  const { projectId } = useProjectParams();
  const invalidate = useInvalidateDatasets();
  return useMutation({
    mutationFn: ({ datasetId, file }: { datasetId: string; file: File }) =>
      datasetsApi.uploadItems(projectId, datasetId, file),
    onSuccess: invalidate,
  });
}

export function useAddItemsFromTraces() {
  const { projectId } = useProjectParams();
  const invalidate = useInvalidateDatasets();
  return useMutation({
    mutationFn: ({
      datasetId,
      traceIds,
      spanId,
    }: {
      datasetId: string;
      traceIds: string[];
      spanId?: string | null;
    }) => datasetsApi.addItemsFromTraces(projectId, datasetId, traceIds, spanId),
    onSuccess: invalidate,
  });
}

/** The review queues a picker offers. */
export function useQueueOptionsQuery(enabled = true) {
  const { projectId } = useProjectParams();
  return useQuery({
    queryKey: [...queryKeys.project(projectId).annotations.all, "options"],
    queryFn: () => annotationsApi.list(projectId, { limit: OPTION_LIMIT }),
    enabled,
  });
}

export function useAddToQueue() {
  const { projectId } = useProjectParams();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({
      queueId,
      traceIds,
      spanId,
    }: {
      queueId: string;
      traceIds: string[];
      spanId?: string | null;
    }) => annotationsApi.addItems(projectId, queueId, traceIds, spanId),
    onSuccess: () =>
      queryClient.invalidateQueries({ queryKey: queryKeys.project(projectId).annotations.all }),
  });
}
