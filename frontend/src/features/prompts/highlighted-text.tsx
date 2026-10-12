import { highlightParts } from "@/lib/prompt-template";

/** Template text with each `{{name}}` reference in violet. Whitespace and newlines are kept. */
export function HighlightedText({ text }: { text: string }) {
  return (
    <>
      {highlightParts(text).map((part, index) =>
        part.variable ? (
          <span key={index} className="text-accent">
            {part.text}
          </span>
        ) : (
          <span key={index}>{part.text}</span>
        ),
      )}
    </>
  );
}
