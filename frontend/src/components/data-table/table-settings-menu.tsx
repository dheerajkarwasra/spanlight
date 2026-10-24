import { SlidersHorizontal } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuCheckboxItem,
  DropdownMenuContent,
  DropdownMenuLabel,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Tooltip } from "@/components/ui/tooltip";

import type { DataColumn } from "./columns";
import type { Density } from "./use-table-prefs";

interface TableSettingsMenuProps<Row> {
  /** The hideable columns, in table order. */
  columns: DataColumn<Row>[];
  isVisible: (colId: string) => boolean;
  setVisible: (colId: string, visible: boolean) => void;
  density: Density;
  setDensity: (density: Density) => void;
}

/** The "Table settings" button: which columns show, and row density. */
export function TableSettingsMenu<Row>({
  columns,
  isVisible,
  setVisible,
  density,
  setDensity,
}: TableSettingsMenuProps<Row>) {
  return (
    <DropdownMenu>
      <Tooltip content="Table settings">
        <DropdownMenuTrigger asChild>
          <Button variant="ghost" size="icon-sm" aria-label="Table settings">
            <SlidersHorizontal aria-hidden />
          </Button>
        </DropdownMenuTrigger>
      </Tooltip>
      <DropdownMenuContent align="end">
        {columns.length > 0 ? (
          <>
            <DropdownMenuLabel>Columns</DropdownMenuLabel>
            {columns.map((col) => (
              <DropdownMenuCheckboxItem
                key={col.id}
                checked={isVisible(col.id)}
                onCheckedChange={(checked) => {
                  setVisible(col.id, checked);
                }}
                // Keep the menu open so several columns can be toggled in one go.
                onSelect={(event) => {
                  event.preventDefault();
                }}
              >
                {col.header}
              </DropdownMenuCheckboxItem>
            ))}
            <DropdownMenuSeparator />
          </>
        ) : null}
        <DropdownMenuLabel>Density</DropdownMenuLabel>
        <DropdownMenuRadioGroup
          value={density}
          onValueChange={(value) => {
            setDensity(value as Density);
          }}
        >
          <DropdownMenuRadioItem value="comfortable">Comfortable</DropdownMenuRadioItem>
          <DropdownMenuRadioItem value="compact">Compact</DropdownMenuRadioItem>
        </DropdownMenuRadioGroup>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
