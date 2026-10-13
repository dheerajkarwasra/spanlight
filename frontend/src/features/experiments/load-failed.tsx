import { Notice } from "@/components/notice";
import { Button } from "@/components/ui/button";

/** A list the form needs did not load: say which, and offer to fetch it again. */
export function LoadFailed({ what, onRetry }: { what: string; onRetry: () => void }) {
  return (
    <Notice tone="danger" role="alert">
      Couldn't load the {what}.{" "}
      <Button variant="link" size="sm" onClick={onRetry}>
        Try again
      </Button>
    </Notice>
  );
}
