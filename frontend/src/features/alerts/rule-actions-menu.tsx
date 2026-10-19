import { Ellipsis } from "lucide-react";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import type { AlertRule } from "@/lib/api";

import { DeleteRuleDialog } from "./delete-rule-dialog";

interface RuleActionsMenuProps {
  rule: AlertRule;
}

/** Figma "More rule actions": the "…" after Edit, with "Delete rule" behind a confirm. */
export function RuleActionsMenu({ rule }: RuleActionsMenuProps) {
  const [deleting, setDeleting] = useState(false);

  return (
    <>
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <Button size="icon" aria-label={`More actions for ${rule.name}`}>
            <Ellipsis aria-hidden />
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="end" className="w-56">
          <DropdownMenuItem
            destructive
            onSelect={() => {
              setDeleting(true);
            }}
          >
            Delete rule
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>
      <DeleteRuleDialog rule={rule} open={deleting} onOpenChange={setDeleting} />
    </>
  );
}
