import { X } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import { Notice } from "@/components/notice";
import { Button } from "@/components/ui/button";
import {
  Sheet,
  SheetClose,
  SheetContent,
  SheetDescription,
  SheetTitle,
} from "@/components/ui/sheet";
import type { DatasetItem } from "@/lib/api";
import { formatDate, shortId } from "@/lib/format";

import { datasetErrorMessage } from "./dataset-errors";
import { useAddItems, useUpdateItem } from "./dataset-queries";
import {
  draftChanges,
  draftDirty,
  draftFor,
  draftNewItem,
  draftProblems,
  type ItemDraft,
} from "./item-draft";
import { DeleteItemButton, PayloadField, SourceTraceTile } from "./item-editor-parts";
import { MetadataEditor } from "./metadata-editor";

/** What the sheet edits: an existing item with its row number, or a new item. */
export type ItemEditorTarget = { item: DatasetItem; number: number } | { item: null };

interface ItemEditorProps {
  datasetId: string;
  target: ItemEditorTarget | null;
  /** Without `datasets:write` the sheet only shows the item. */
  canWrite: boolean;
  onClose: () => void;
}

/** Figma "Datasets — Item editor sheet": edit the expected output and metadata, or add an item. */
export function ItemEditor({ datasetId, target, canWrite, onClose }: ItemEditorProps) {
  const [pending, setPending] = useState(false);
  return (
    <Sheet
      open={target !== null}
      onOpenChange={(open) => {
        if (!open && !pending) onClose();
      }}
    >
      <SheetContent side="right" hideClose className="w-[520px] gap-5 p-6 sm:p-7">
        {target ? (
          <ItemForm
            key={target.item?.id ?? "new"}
            datasetId={datasetId}
            target={target}
            canWrite={canWrite}
            onPendingChange={setPending}
            onDone={onClose}
          />
        ) : null}
      </SheetContent>
    </Sheet>
  );
}

interface ItemFormProps {
  datasetId: string;
  target: ItemEditorTarget;
  canWrite: boolean;
  onPendingChange: (pending: boolean) => void;
  onDone: () => void;
}

function ItemForm({ datasetId, target, canWrite, onPendingChange, onDone }: ItemFormProps) {
  const item = target.item;
  const [draft, setDraft] = useState<ItemDraft>(() => draftFor(item));
  const add = useAddItems(datasetId);
  const update = useUpdateItem(datasetId);
  const mutation = item ? update : add;
  const problems = draftProblems(item, draft);
  const blocked = Boolean(problems.input ?? problems.expected ?? problems.metadata);
  const edit = (patch: Partial<ItemDraft>) => {
    setDraft((current) => ({ ...current, ...patch }));
  };

  function save() {
    const settle = {
      onSuccess: () => {
        toast.success(item ? "Saved the item." : "Added the item.");
        onDone();
      },
      onSettled: () => {
        onPendingChange(false);
      },
    };
    onPendingChange(true);
    if (item) {
      update.mutate({ itemId: item.id, update: draftChanges(item, draft) }, settle);
    } else {
      add.mutate([draftNewItem(draft)], settle);
    }
  }

  return (
    <div className="flex min-h-full flex-col gap-5">
      <div className="flex items-start justify-between gap-4">
        <div className="flex min-w-0 flex-col gap-1">
          <SheetTitle className="text-h2 text-foreground">
            {sheetTitle(target, canWrite)}
          </SheetTitle>
          <SheetDescription className="text-sm text-muted-foreground">
            {item ? itemOrigin(item) : "Add an input and, if you have one, the expected answer."}
          </SheetDescription>
        </div>
        <SheetClose asChild>
          <Button variant="ghost" size="icon-sm" aria-label="Close" className="bg-surface-muted">
            <X aria-hidden />
          </Button>
        </SheetClose>
      </div>
      <PayloadField
        label="Input"
        value={draft.input}
        readOnly={item !== null || !canWrite}
        problem={problems.input}
        onChange={(input) => {
          edit({ input });
        }}
      />
      <PayloadField
        label="Expected output"
        value={draft.expected}
        readOnly={!canWrite}
        problem={problems.expected}
        onChange={(expected) => {
          edit({ expected });
        }}
        hint={canWrite ? expectedHint(item) : undefined}
      />
      <MetadataEditor
        value={draft.metadata}
        readOnly={!canWrite}
        problem={problems.metadata}
        onChange={(metadata) => {
          edit({ metadata });
        }}
      />
      {item?.source_trace_id ? (
        <SourceTraceTile traceId={item.source_trace_id} spanId={item.source_span_id} />
      ) : null}
      {mutation.error ? (
        <Notice tone="danger" role="alert">
          {datasetErrorMessage(mutation.error)}
        </Notice>
      ) : null}
      <div className="mt-auto flex flex-wrap items-center gap-2.5 pt-2">
        {item && canWrite ? (
          <DeleteItemButton datasetId={datasetId} item={item} onDeleted={onDone} />
        ) : null}
        <SheetClose asChild>
          <Button className="ml-auto" disabled={mutation.isPending}>
            {canWrite ? "Cancel" : "Close"}
          </Button>
        </SheetClose>
        {canWrite ? (
          <Button
            variant="primary"
            disabled={!draftDirty(item, draft) || blocked}
            loading={mutation.isPending}
            onClick={save}
          >
            {item ? "Save changes" : "Add item"}
          </Button>
        ) : null}
      </div>
    </div>
  );
}

function sheetTitle(target: ItemEditorTarget, canWrite: boolean): string {
  if (!("number" in target)) return "New item";
  return canWrite ? `Edit item ${String(target.number)}` : `Item ${String(target.number)}`;
}

function expectedHint(item: DatasetItem | null): string {
  return item?.source_trace_id
    ? "Edit the expected output; the recorded output is only a starting point."
    : "Leave it empty if there is no single right answer yet.";
}

function itemOrigin(item: DatasetItem): string {
  const added = formatDate(item.created_at);
  const when = added ? `added ${added}` : "added on an unknown date";
  return item.source_trace_id
    ? `From trace ${shortId(item.source_trace_id)} · ${when}`
    : `Not from a trace · ${when}`;
}
