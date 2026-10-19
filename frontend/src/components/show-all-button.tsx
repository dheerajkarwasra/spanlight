import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

interface ShowAllButtonProps {
  expanded: boolean;
  onToggle: () => void;
  /** What expanding shows: "Show all {label}", e.g. "1,048,576 characters". */
  label: string;
  className?: string;
}

/**
 * The "Show all" link of clamped content (Figma "Traces/Chat bubble"), for any long text, string
 * or list, so a large payload renders its start until asked for the rest.
 */
export function ShowAllButton({ expanded, onToggle, label, className }: ShowAllButtonProps) {
  return (
    <Button
      variant="link"
      size="sm"
      aria-expanded={expanded}
      onClick={onToggle}
      className={cn("self-start", className)}
    >
      {expanded ? "Show less" : `Show all ${label}`}
    </Button>
  );
}
