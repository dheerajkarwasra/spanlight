import { Lock, PencilLine, Undo2 } from "lucide-react";
import { useId } from "react";

import { JsonViewer } from "@/components/json-viewer";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";

import { ReplayBodyEditor } from "./replay-body-editor";

interface ReplayRequestProps {
  /** The request text: the stored body's JSON, or the edit. */
  text: string;
  /** The stored request, shown read-only until "Edit"; null when only an edit can be sent. */
  stored: Record<string, unknown> | null;
  editing: boolean;
  /** Start editing (`text`), keep editing (new text) or undo every edit (null). */
  onEdit?: (text: string | null) => void;
}

/**
 * Figma "Playground / Replay column (read-only, editing)": the stored request read-only with
 * "Edit", or the JSON editor with "Undo edits". Without `onEdit` (a viewer) it stays read-only.
 */
export function ReplayRequest({ text, stored, editing, onEdit }: ReplayRequestProps) {
  const headingId = useId();
  return (
    <section aria-labelledby={headingId} className="flex flex-col gap-2.5">
      <div className="flex min-h-9 flex-wrap items-center gap-2">
        <h3 id={headingId} className="text-sm font-semibold text-foreground">
          Request
        </h3>
        {editing ? (
          <Badge variant="warning">
            <PencilLine aria-hidden />
            Edited
          </Badge>
        ) : (
          <Badge variant="outline">
            <Lock aria-hidden />
            Stored, read-only
          </Badge>
        )}
        {onEdit && editing ? (
          <Button variant="ghost" size="sm" className="ml-auto" onClick={() => onEdit(null)}>
            <Undo2 aria-hidden />
            Undo edits
          </Button>
        ) : null}
        {onEdit && !editing ? (
          <Button variant="secondary" size="sm" className="ml-auto" onClick={() => onEdit(text)}>
            <PencilLine aria-hidden />
            Edit
          </Button>
        ) : null}
      </div>
      {editing && onEdit ? (
        <ReplayBodyEditor text={text} onChange={onEdit} />
      ) : (
        <JsonViewer value={stored} className="max-h-96 overflow-y-auto" />
      )}
    </section>
  );
}
