import { Send } from "lucide-react";
import { useState } from "react";

import { Notice } from "@/components/notice";
import { Button } from "@/components/ui/button";

import { INK_FOCUS } from "./api-key-styles";
import { sendTestTrace, testTraceBody, type IngestResult, type TestTraceIds } from "./test-trace";

interface TestTraceButtonProps {
  /** The just-created key's secret. Without it there is nothing to send with. */
  apiKey: string | null;
  /** Owned by the parent with the key, so a remount (tab switch) keeps the same span. */
  ids: TestTraceIds;
  sent: boolean;
  environment: string;
  onSent: () => void;
}

type SendState = "idle" | "sending" | Extract<IngestResult, { ok: false }>;

/**
 * "Send a test trace" on the API key card: posts one span with the key on screen so the
 * waiting row can flip before any code runs. Every retry reuses `ids`, so it upserts the same
 * span.
 */
export function TestTraceButton({ apiKey, ids, sent, environment, onSent }: TestTraceButtonProps) {
  const [state, setState] = useState<SendState>("idle");

  if (!apiKey) {
    return null;
  }

  const error = typeof state === "object" ? state : null;

  const send = async () => {
    setState("sending");
    const result = await sendTestTrace(apiKey, testTraceBody(ids, environment, new Date()));
    if (result.ok) {
      setState("idle");
      onSent();
    } else {
      setState(result);
    }
  };

  return (
    <div className="flex flex-col items-start gap-2">
      <Button
        size="sm"
        loading={state === "sending"}
        disabled={sent || (error !== null && !error.retry)}
        className={INK_FOCUS}
        onClick={() => {
          void send();
        }}
      >
        {state === "sending" ? null : <Send aria-hidden />}
        {error?.retry ? "Retry" : "Send a test trace"}
      </Button>
      {error ? (
        <Notice tone="danger" role="alert" className="w-full">
          {error.message}
        </Notice>
      ) : null}
    </div>
  );
}
