import type { ReactNode, Ref } from "react";

import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

export interface FieldSelectOption {
  value: string;
  label: string;
  /** Shown before the label in the list only (a colour dot). */
  leading?: ReactNode;
  /** Shown after the label in the list only (the stored value in mono, or why it is disabled). */
  trailing?: ReactNode;
  /** Listed but not selectable; kept readable, so `trailing` should say why. */
  disabled?: boolean;
}

interface FieldSelectProps {
  /** React Hook Form's field ref: focuses the trigger when a refused save lands here first. */
  ref?: Ref<HTMLButtonElement>;
  value: string;
  onChange: (value: string) => void;
  options: readonly FieldSelectOption[];
  /** Shown while `value` is empty. */
  placeholder?: string;
  /** Wired by `FormField`. */
  id?: string;
  "aria-invalid"?: boolean;
  "aria-describedby"?: string;
}

/** Figma "Gateway/Select field" inside a `FormField`: the label, hint and error reach the trigger. */
export function FieldSelect({
  ref,
  value,
  onChange,
  options,
  placeholder,
  id,
  "aria-invalid": ariaInvalid,
  "aria-describedby": ariaDescribedBy,
}: FieldSelectProps) {
  return (
    <Select value={value} onValueChange={onChange}>
      <SelectTrigger
        ref={ref}
        id={id}
        aria-invalid={ariaInvalid}
        aria-describedby={ariaDescribedBy}
        className="aria-invalid:border-[1.5px] aria-invalid:border-danger"
      >
        <SelectValue placeholder={placeholder} />
      </SelectTrigger>
      <SelectContent>
        {options.map((option) => (
          <SelectItem
            key={option.value}
            value={option.value}
            leading={option.leading}
            trailing={option.trailing}
            disabled={option.disabled}
            className={
              option.disabled ? "text-subtle-foreground data-[disabled]:opacity-100" : undefined
            }
          >
            {option.label}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  );
}
