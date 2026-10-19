import { useState } from "react";

import { Button } from "@/components/ui/button";
import { useProjectParams } from "@/features/shell";

import { RuleEditorDialog } from "./rule-editor-dialog";

/** "Create rule": opens the rule editor. Render it only for people with `alerts:write`. */
export function CreateRuleButton({ className }: { className?: string }) {
  const { projectId } = useProjectParams();
  const [open, setOpen] = useState(false);

  return (
    <>
      <Button
        variant="primary"
        className={className}
        onClick={() => {
          setOpen(true);
        }}
      >
        Create rule
      </Button>
      <RuleEditorDialog open={open} onOpenChange={setOpen} projectId={projectId} />
    </>
  );
}
