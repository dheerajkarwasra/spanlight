import { Play } from "lucide-react";
import { useState } from "react";

import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from "@/components/ui/alert-dialog";
import { Button } from "@/components/ui/button";
import { useCurrentOrg } from "@/features/shell";

import { useMe } from "./queries";
import { useDemoSession } from "./use-demo-session";

/**
 * "See it in the live demo" signs the browser in as the shared demo visitor, which ends the
 * current session, so it asks first. Hidden inside the demo org itself.
 */
export function LiveDemoButton() {
  const org = useCurrentOrg();
  const me = useMe();
  const demo = useDemoSession();
  const [open, setOpen] = useState(false);

  if (!org || org.is_demo) {
    return null;
  }
  return (
    <AlertDialog open={open} onOpenChange={setOpen}>
      <AlertDialogTrigger asChild>
        <Button variant="secondary">
          <Play aria-hidden />
          See it in the live demo
        </Button>
      </AlertDialogTrigger>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>Open the live demo?</AlertDialogTitle>
          <AlertDialogDescription>
            The demo is a shared, read-only workspace with sample traces. Opening it signs you out
            of {me.user.email}; sign back in any time to return to this project.
          </AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel>Stay here</AlertDialogCancel>
          <AlertDialogAction
            onClick={(event) => {
              // Keep the dialog open with a spinner until the session switch finishes.
              event.preventDefault();
              demo.mutate(undefined, {
                onSettled: () => {
                  setOpen(false);
                },
              });
            }}
            disabled={demo.isPending}
            aria-busy={demo.isPending || undefined}
          >
            {demo.isPending ? "Opening…" : "Open the demo"}
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}
