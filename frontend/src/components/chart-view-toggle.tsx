import { Button } from "@/components/ui/button";
import type { ChartView } from "@/lib/use-stored-choice";

interface ChartViewToggleProps {
  view: ChartView;
  onChange: (view: ChartView) => void;
}

/**
 * The "Show table" / "Show chart" button of a chart card. The label names what a press shows, so
 * it carries no `aria-pressed` (a label swap and a pressed state together read as a double
 * negative). 44 px tall on phones.
 */
export function ChartViewToggle({ view, onChange }: ChartViewToggleProps) {
  return (
    <Button
      variant="ghost"
      size="sm"
      className="max-md:min-h-11"
      onClick={() => {
        onChange(view === "table" ? "chart" : "table");
      }}
    >
      {view === "table" ? "Show chart" : "Show table"}
    </Button>
  );
}
