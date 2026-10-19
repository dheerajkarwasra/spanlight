import { CircleAlert, CircleCheck, TriangleAlert } from "lucide-react";

import { RowAction } from "@/components/tile-list";
import { errorMessage, isApiError } from "@/lib/api";
import { cn } from "@/lib/utils";

import { testResultView, type TestResultView } from "./delivery-status";
import type { useTestChannel } from "./channels-queries";

type TestMutation = ReturnType<typeof useTestChannel>;

interface TestSendButtonProps {
  test: TestMutation;
  channelName: string;
}

/** The row's "Test" action. The outcome shows under the row (`TestSendResult`). */
export function TestSendButton({ test, channelName }: TestSendButtonProps) {
  return (
    <RowAction
      loading={test.isPending}
      aria-label={`Send a test to ${channelName}`}
      onClick={() => {
        test.mutate();
      }}
    >
      Test
    </RowAction>
  );
}

const TONE_CLASSES: Record<TestResultView["tone"], string> = {
  success: "bg-success-subtle text-success",
  warning: "bg-warning-subtle text-warning",
  danger: "bg-danger-subtle text-danger-text",
};

const TONE_ICONS = { success: CircleCheck, warning: TriangleAlert, danger: CircleAlert };

/** What a request failure means for a test: 10 an hour per channel, then `429 RATE_LIMITED`. */
function failureView(error: unknown): TestResultView {
  if (isApiError(error) && error.code === "RATE_LIMITED") {
    return {
      tone: "warning",
      message: "Too many tests: a channel takes 10 an hour. Try again later.",
    };
  }
  return { tone: "danger", message: `Couldn't send the test: ${errorMessage(error)}` };
}

/**
 * Figma "Alerts — Channels — test-send results": a tinted tile under the row with the §11.1 copy.
 * Announced politely when it appears.
 */
export function TestSendResult({ test }: { test: TestMutation }) {
  const view = test.data
    ? testResultView(test.data)
    : test.isError
      ? failureView(test.error)
      : null;
  return (
    <div role="status" aria-live="polite">
      {view ? <ResultTile view={view} /> : null}
    </div>
  );
}

function ResultTile({ view }: { view: TestResultView }) {
  const Icon = TONE_ICONS[view.tone];
  return (
    <p
      className={cn(
        "flex items-start gap-2.5 rounded-tile px-4 py-3 text-sm font-medium [overflow-wrap:anywhere]",
        TONE_CLASSES[view.tone],
      )}
    >
      <Icon aria-hidden className="mt-0.5 size-4 shrink-0" strokeWidth={2} />
      {view.message}
    </p>
  );
}
