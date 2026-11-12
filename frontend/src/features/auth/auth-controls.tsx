import {
  CircleAlert,
  CircleCheck,
  Eye,
  EyeOff,
  Info,
  TriangleAlert,
  type LucideIcon,
} from "lucide-react";
import {
  cloneElement,
  useId,
  useState,
  type ComponentProps,
  type ReactElement,
  type ReactNode,
} from "react";

import { Button as AppButton, type ButtonProps } from "@/components/ui/button";
import { Input as AppInput } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { BUTTON_BASE } from "@/features/landing/button-styles";
import { cn } from "@/lib/utils";

/*
 * The app's form controls in the Auth v2 look (Figma "Auth v2 — Components"). They keep the app
 * components' names, props and wiring, so an auth page only changes where it imports them from.
 */

/** A text link or text button: 15px medium, underlined (Figma "Switch", "Back to sign in"). */
export const AUTH_LINK =
  "auth-tap lp-control rounded-xs text-[15px] leading-[1.4] font-medium text-mono-900 underline underline-offset-2 hover:text-mono-600";

/** Small print under a control: 13px grey. */
export const AUTH_NOTE = "text-[13px] leading-normal text-mono-600";

/** Field label: 14px medium. */
export const AUTH_LABEL = "text-sm leading-[1.4] font-medium text-mono-900";

type ButtonVariant = NonNullable<ButtonProps["variant"]>;

/** Auth/Button: 48px pills. `link` and the rest keep the app look, in mono through the tokens. */
const BUTTON_SKINS: Partial<Record<ButtonVariant, string>> = {
  primary: "border-mono-black bg-mono-black text-mono-white hover:bg-mono-800",
  secondary:
    "border-mono-line-strong bg-mono-white text-mono-900 shadow-none hover:border-mono-500 hover:bg-mono-white",
  ghost: "border-transparent text-mono-900 hover:bg-mono-mist",
};

export function Button({ variant, className, ...props }: ButtonProps) {
  const skin = BUTTON_SKINS[variant ?? "secondary"];
  return (
    <AppButton
      variant={variant}
      // The landing button base brings the press scale, so the app button's own is turned off.
      className={cn(skin && [BUTTON_BASE, "h-12 gap-2.5 active:scale-100", skin], className)}
      {...props}
    />
  );
}

export function Input({ className, ...props }: ComponentProps<"input">) {
  return <AppInput className={cn("auth-input px-3.5", className)} {...props} />;
}

/**
 * A password field with a show/hide toggle. The toggle never submits the form, and the input keeps
 * its autocomplete purpose in both states, so password managers and paste still work.
 */
export function PasswordInput({ className, ...props }: Omit<ComponentProps<"input">, "type">) {
  const [shown, setShown] = useState(false);
  const Icon = shown ? EyeOff : Eye;
  return (
    <div className="relative">
      <Input type={shown ? "text" : "password"} className={cn("pr-12", className)} {...props} />
      <button
        type="button"
        aria-pressed={shown}
        aria-label={shown ? "Hide password" : "Show password"}
        onClick={() => {
          setShown(!shown);
        }}
        className="lp-control absolute top-0.5 right-0.5 flex size-11 items-center justify-center rounded-lg text-mono-600 hover:text-mono-900"
      >
        <Icon aria-hidden className="size-5" strokeWidth={1.75} />
      </button>
    </div>
  );
}

/** The line under a field that says what is wrong with it: an icon and text, never colour alone. */
export function FieldError({
  id,
  role,
  children,
}: {
  id: string;
  role?: "alert";
  children: ReactNode;
}) {
  return (
    <p
      id={id}
      role={role}
      className="auth-fade flex items-start gap-1.5 text-[13px] leading-[1.4] text-mono-900"
    >
      <CircleAlert aria-hidden className="mt-0.5 size-3.5 shrink-0" strokeWidth={2.25} />
      {children}
    </p>
  );
}

interface FormFieldProps {
  label: ReactNode;
  /** The input element. It receives id, aria-invalid and aria-describedby. */
  children: ReactElement<{
    id?: string;
    "aria-invalid"?: boolean;
    "aria-describedby"?: string;
  }>;
  error?: string | undefined;
  hint?: ReactNode;
  labelAction?: ReactNode;
}

/** Label, control and hint or error, wired together like the app's `FormField`. */
export function FormField({ label, children, error, hint, labelAction }: FormFieldProps) {
  const id = useId();
  const hintId = `${id}-hint`;
  const errorId = `${id}-error`;
  const describedBy = [hint ? hintId : null, error ? errorId : null].filter(Boolean).join(" ");

  return (
    <div className="flex flex-col gap-2">
      <div className="flex items-center justify-between gap-2">
        <Label htmlFor={id} className={AUTH_LABEL}>
          {label}
        </Label>
        {labelAction}
      </div>
      {cloneElement(children, {
        id,
        "aria-invalid": error ? true : undefined,
        "aria-describedby": describedBy || undefined,
      })}
      {hint && !error ? (
        <p id={hintId} className={AUTH_NOTE}>
          {hint}
        </p>
      ) : null}
      {error ? <FieldError id={errorId}>{error}</FieldError> : null}
    </div>
  );
}

type CalloutTone = "info" | "success" | "warning" | "danger";

const CALLOUT_ICONS: Record<CalloutTone, LucideIcon> = {
  info: Info,
  success: CircleCheck,
  warning: TriangleAlert,
  danger: CircleAlert,
};

interface CalloutProps {
  tone?: CalloutTone;
  /** A semibold first line, e.g. "Not available on this server". */
  title?: ReactNode;
  /** `alert` for an error that appears after a person acted; `status` for a polite note. */
  role?: "alert" | "status";
  children: ReactNode;
}

/**
 * Auth/Form error (`danger`: the #171717 border) and Auth/Form status (the rest). The icon and the
 * words carry the meaning.
 */
export function Callout({ tone = "info", title, role, children }: CalloutProps) {
  const Icon = CALLOUT_ICONS[tone];
  return (
    <div
      role={role}
      className={cn(
        "auth-fade flex items-start gap-2.5 rounded-[10px] border bg-mono-mist px-3.5 py-3 text-sm leading-[1.4] font-medium text-mono-900",
        tone === "danger" ? "border-mono-900" : "border-mono-line-strong",
      )}
    >
      <Icon aria-hidden className="mt-px size-[18px] shrink-0" strokeWidth={1.75} />
      <div className="min-w-0 flex-1 [overflow-wrap:anywhere]">
        {title ? <p className="font-semibold">{title}</p> : null}
        {children}
      </div>
    </div>
  );
}
