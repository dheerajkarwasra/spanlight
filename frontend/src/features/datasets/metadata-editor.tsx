import { CircleAlert, Plus, X } from "lucide-react";
import { useId, useState } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";

import { MAX_METADATA_BYTES, jsonBytes, metadataChip, sizeLabel } from "./item-values";

interface MetadataEditorProps {
  value: Record<string, unknown>;
  onChange: (value: Record<string, unknown>) => void;
  /** Viewers see the pairs without the add and remove controls. */
  readOnly?: boolean;
  /** Why the metadata can't be saved (over 8 KB). */
  problem?: string | null;
}

/**
 * Figma item editor "Metadata": the pairs as mono chips with a remove mark, and "Add key" for a
 * new text pair. A pair with the same key replaces the old one.
 */
export function MetadataEditor({
  value,
  onChange,
  readOnly = false,
  problem = null,
}: MetadataEditorProps) {
  const labelId = useId();
  const problemId = useId();
  const [adding, setAdding] = useState(false);
  const entries = Object.entries(value);
  const bytes = jsonBytes(value);

  function remove(key: string) {
    onChange(Object.fromEntries(entries.filter(([name]) => name !== key)));
  }

  return (
    <div
      role="group"
      aria-labelledby={labelId}
      aria-describedby={problem ? problemId : undefined}
      className="flex flex-col gap-2"
    >
      <div className="flex items-baseline justify-between gap-2">
        <span id={labelId} className="text-label font-semibold text-foreground">
          Metadata
        </span>
        <span
          className={
            bytes > MAX_METADATA_BYTES
              ? "text-xs font-medium text-danger-text"
              : "text-xs text-muted-foreground"
          }
        >
          {sizeLabel(bytes, MAX_METADATA_BYTES)}
        </span>
      </div>
      <ul className="flex flex-wrap gap-1.5">
        {entries.map(([key, entry]) => (
          <li
            key={key}
            className="flex max-w-full items-center gap-1 rounded-full border border-border-strong py-0.5 pr-0.5 pl-2.5 font-mono text-xs text-foreground"
          >
            <span className={readOnly ? "truncate pr-2" : "truncate"}>
              {metadataChip(key, entry)}
            </span>
            {readOnly ? null : (
              <Button
                variant="ghost"
                size="icon-sm"
                aria-label={`Remove ${key}`}
                className="size-6"
                onClick={() => {
                  remove(key);
                }}
              >
                <X aria-hidden />
              </Button>
            )}
          </li>
        ))}
        {entries.length === 0 && readOnly ? (
          <li className="text-xs text-muted-foreground">No metadata.</li>
        ) : null}
        {adding || readOnly ? null : (
          <li>
            <Button
              variant="ghost"
              size="sm"
              className="h-7 border border-accent px-2.5 text-xs text-accent"
              onClick={() => {
                setAdding(true);
              }}
            >
              <Plus aria-hidden />
              Add key
            </Button>
          </li>
        )}
      </ul>
      {adding ? (
        <AddPair
          onAdd={(key, text) => {
            onChange({ ...value, [key]: text });
            setAdding(false);
          }}
          onCancel={() => {
            setAdding(false);
          }}
        />
      ) : null}
      {problem ? (
        <p id={problemId} className="flex items-start gap-1.5 text-xs font-medium text-danger-text">
          <CircleAlert aria-hidden className="mt-px size-3.5 shrink-0" />
          {problem}
        </p>
      ) : null}
    </div>
  );
}

function AddPair({
  onAdd,
  onCancel,
}: {
  onAdd: (key: string, value: string) => void;
  onCancel: () => void;
}) {
  const [key, setKey] = useState("");
  const [text, setText] = useState("");
  const trimmedKey = key.trim();
  return (
    <div className="flex flex-col gap-2 rounded-tile bg-surface-muted p-3 sm:flex-row sm:items-center">
      <Input
        aria-label="Key"
        placeholder="key"
        value={key}
        autoFocus
        className="h-9 font-mono text-label sm:w-36"
        onChange={(event) => {
          setKey(event.target.value);
        }}
      />
      <Input
        aria-label="Value"
        placeholder="value"
        value={text}
        className="h-9 font-mono text-label"
        onChange={(event) => {
          setText(event.target.value);
        }}
      />
      <div className="flex shrink-0 gap-1.5">
        <Button size="sm" variant="ghost" onClick={onCancel}>
          Cancel
        </Button>
        <Button
          size="sm"
          variant="primary"
          disabled={!trimmedKey}
          onClick={() => {
            onAdd(trimmedKey, text);
          }}
        >
          Add
        </Button>
      </div>
    </div>
  );
}
