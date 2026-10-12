import type { ReactNode } from "react";

import { FieldSelect } from "@/components/field-select";
import { FormField } from "@/components/form-field";
import { Notice } from "@/components/notice";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";

import { useDatasetOptionsQuery } from "./dataset-queries";

interface DatasetSelectProps {
  value: string;
  onChange: (datasetId: string) => void;
  hint?: ReactNode;
}

/** The "Dataset" picker of the upload and add-to-dataset dialogs, with its loading and error. */
export function DatasetSelect({ value, onChange, hint }: DatasetSelectProps) {
  const query = useDatasetOptionsQuery();

  if (query.isPending) {
    return (
      <div className="flex flex-col gap-2" aria-busy>
        <span className="text-label font-semibold text-foreground">Dataset</span>
        <Skeleton className="h-[46px] w-full rounded-input" />
      </div>
    );
  }
  if (query.isError) {
    return (
      <Notice tone="danger" role="alert">
        Couldn't load the datasets.{" "}
        <Button variant="link" size="sm" onClick={() => void query.refetch()}>
          Try again
        </Button>
      </Notice>
    );
  }
  if (query.data.items.length === 0) {
    return (
      <Notice>This project has no datasets yet. Create one on the Datasets page first.</Notice>
    );
  }
  return (
    <FormField label="Dataset" hint={hint}>
      <FieldSelect
        value={value}
        onChange={onChange}
        placeholder="Pick a dataset"
        options={query.data.items.map((dataset) => ({ value: dataset.id, label: dataset.name }))}
      />
    </FormField>
  );
}
