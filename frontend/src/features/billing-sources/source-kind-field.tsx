import { RadioGroup as RadioGroupPrimitive } from "radix-ui";
import { useId } from "react";

import type { BillingSourceKind } from "@/lib/api";
import { cn } from "@/lib/utils";

import { isApiKind } from "./source-form";
import { KIND_CHOICES } from "./source-model";

export const API_KINDS_OFF_COPY =
  "API kinds are unavailable until the server key is set: billing sources that fetch from a provider are encrypted with CREDENTIALS_KEYS, which isn’t set.";

interface SourceKindFieldProps {
  value: BillingSourceKind;
  onChange: (kind: BillingSourceKind) => void;
  /** The server has no CREDENTIALS_KEYS: the API kinds are offered disabled, with the reason. */
  apiKindsOff: boolean;
}

/**
 * Figma "Kind": three "Settings/Choice card" radios, Anthropic Admin API, OpenAI Admin API and
 * CSV upload. Focus starts here when the dialog opens.
 */
export function SourceKindField({ value, onChange, apiKindsOff }: SourceKindFieldProps) {
  const labelId = useId();
  const reasonId = useId();

  return (
    <div className="flex flex-col gap-2">
      <span id={labelId} className="text-label leading-5 font-semibold text-foreground">
        Kind
      </span>
      <RadioGroupPrimitive.Root
        value={value}
        onValueChange={(next) => {
          const choice = KIND_CHOICES.find((candidate) => candidate.value === next);
          if (choice) {
            onChange(choice.value);
          }
        }}
        aria-labelledby={labelId}
        className="grid gap-2 sm:grid-cols-3"
      >
        {KIND_CHOICES.map((choice) => (
          <KindCard
            key={choice.value}
            choice={choice}
            disabled={apiKindsOff && isApiKind(choice.value)}
            reasonId={reasonId}
          />
        ))}
      </RadioGroupPrimitive.Root>
      {apiKindsOff ? (
        <p id={reasonId} className="text-xs font-medium text-warning">
          {API_KINDS_OFF_COPY}
        </p>
      ) : null}
    </div>
  );
}

interface KindCardProps {
  choice: (typeof KIND_CHOICES)[number];
  disabled: boolean;
  reasonId: string;
}

function KindCard({ choice, disabled, reasonId }: KindCardProps) {
  const titleId = useId();
  const descriptionId = useId();

  return (
    <RadioGroupPrimitive.Item
      value={choice.value}
      disabled={disabled}
      aria-labelledby={titleId}
      aria-describedby={disabled ? `${descriptionId} ${reasonId}` : descriptionId}
      className={cn(
        "group flex items-start gap-3 rounded-input text-left transition-colors",
        // The border grows from 1 to 1.5 px as the card is chosen; the padding gives it back.
        "border border-border-strong bg-surface p-3.5 data-[state=unchecked]:hover:bg-surface-muted",
        "data-[state=checked]:border-[1.5px] data-[state=checked]:border-accent data-[state=checked]:bg-surface-selected data-[state=checked]:p-[13.5px]",
        "data-[disabled]:cursor-not-allowed data-[disabled]:opacity-55 data-[disabled]:hover:bg-surface",
      )}
    >
      <span className="flex h-[21px] w-[18px] shrink-0 items-center justify-center">
        <span
          aria-hidden
          className="flex size-[18px] items-center justify-center rounded-full border-[1.5px] border-subtle-foreground bg-surface group-data-[state=checked]:border-accent group-data-[state=checked]:bg-accent"
        >
          <span className="hidden size-1.5 rounded-full bg-accent-foreground group-data-[state=checked]:block" />
        </span>
      </span>
      <span className="flex min-w-0 flex-1 flex-col gap-1">
        <span id={titleId} className="text-sm font-semibold text-foreground">
          {choice.title}
        </span>
        <span id={descriptionId} className="text-xs font-medium text-muted-foreground">
          {choice.description}
        </span>
      </span>
    </RadioGroupPrimitive.Item>
  );
}
