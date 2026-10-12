import { UnknownValue } from "@/components/unknown-value";
import { formatInteger, pluralize } from "@/lib/format";

import { valueText } from "./item-values";
import type { UploadPreview } from "./upload-preview";

/** Figma "Preview · first 5 of N lines": line, input and expected output of the first records. */
export function UploadPreviewTable({ preview }: { preview: UploadPreview }) {
  const shown = preview.rows.length;
  const rejected = preview.total - preview.valid;
  return (
    <div className="flex flex-col gap-2.5">
      <div className="flex flex-wrap items-baseline justify-between gap-2 text-xs">
        <p className="font-semibold text-foreground">
          Preview · first {shown} of {formatInteger(preview.total)} lines
        </p>
        <p className="font-medium text-muted-foreground">
          {rejected > 0
            ? `${pluralize(rejected, "line")} will be rejected. Nothing is saved until you upload.`
            : "Nothing is saved until you upload."}
        </p>
      </div>
      <table className="w-full table-fixed border-separate border-spacing-y-1 rounded-tile bg-surface-muted px-2 pb-1 text-xs">
        <thead>
          <tr className="text-left text-overline text-muted-foreground uppercase">
            <th scope="col" className="w-12 px-2 pt-2 font-semibold">
              Line
            </th>
            <th scope="col" className="px-2 pt-2 font-semibold">
              Input
            </th>
            <th scope="col" className="px-2 pt-2 font-semibold">
              Expected output
            </th>
          </tr>
        </thead>
        <tbody>
          {preview.rows.map((row) => (
            <tr key={row.line} className="bg-surface">
              <td className="rounded-l-input px-2 py-1.5 font-mono text-muted-foreground">
                {row.line}
              </td>
              <td className="truncate px-2 py-1.5 font-medium text-foreground">
                {row.problem ? (
                  <span className="text-danger-text">{row.problem}</span>
                ) : (
                  valueText(row.input)
                )}
              </td>
              <td className="truncate rounded-r-input px-2 py-1.5 text-muted-foreground">
                {row.expectedOutput === null ? (
                  <UnknownValue reason="This line has no expected output" />
                ) : (
                  valueText(row.expectedOutput)
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
