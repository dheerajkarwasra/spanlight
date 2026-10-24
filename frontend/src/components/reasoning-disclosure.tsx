import { ChevronDown, ChevronRight } from "lucide-react";
import { useId, useState } from "react";

interface ReasoningDisclosureProps {
  /** The judge's comment on the score. */
  text: string;
  /** The score it explains; named to screen readers so several disclosures stay apart. */
  scoreName: string;
}

/**
 * Figma "E1": the judge's reasoning under a score, behind a collapsed 24 px "Reasoning" button.
 * Rows without a comment render no disclosure at all.
 */
export function ReasoningDisclosure({ text, scoreName }: ReasoningDisclosureProps) {
  const [open, setOpen] = useState(false);
  const panelId = useId();
  const Chevron = open ? ChevronDown : ChevronRight;

  return (
    <div className="flex min-w-0 flex-col gap-1">
      <button
        type="button"
        aria-expanded={open}
        aria-controls={open ? panelId : undefined}
        onClick={() => {
          setOpen((value) => !value);
        }}
        className="-mx-1 flex min-h-6 w-fit items-center gap-1 rounded-sm px-1 text-xs font-medium text-muted-foreground hover:text-foreground focus-visible:outline-[1.5px] focus-visible:outline-offset-2 focus-visible:outline-ring"
      >
        <Chevron aria-hidden className="size-3.5 shrink-0" />
        Reasoning <span className="sr-only">for {scoreName}</span>
      </button>
      {open ? (
        <p
          id={panelId}
          className="border-l-2 border-border pl-3 text-xs [overflow-wrap:anywhere] whitespace-pre-wrap text-foreground"
        >
          {text}
        </p>
      ) : null}
    </div>
  );
}
