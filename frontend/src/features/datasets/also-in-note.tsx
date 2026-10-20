import { Notice } from "@/components/notice";
import { pluralize } from "@/lib/format";

import { alsoInNames, type AlsoIn } from "./also-in";

interface AlsoInNoteProps {
  accepted: number;
  rejected: number;
  alsoIn: AlsoIn;
}

/**
 * Figma "Add result with the duplicate warning": how many items were added, how many traces were
 * refused, and which other datasets already hold the added traces.
 */
export function AlsoInNote({ accepted, rejected, alsoIn }: AlsoInNoteProps) {
  const added = `Added ${pluralize(accepted, "item")}.`;
  const refused =
    rejected > 0
      ? ` ${pluralize(rejected, "trace")} could not be added; the reasons are below.`
      : "";
  const also =
    alsoIn.length > 0
      ? `${pluralize(alsoIn.length, "trace")} ${alsoIn.length === 1 ? "is" : "are"} also in ${alsoInNames(alsoIn, true)}.`
      : null;
  return (
    <Notice tone="warning" role="status">
      {rejected > 0 ? (
        <>
          <p>
            {added}
            {refused}
          </p>
          {also ? <p>{also}</p> : null}
        </>
      ) : (
        <p>{also ? `${added} ${also}` : added}</p>
      )}
    </Notice>
  );
}
