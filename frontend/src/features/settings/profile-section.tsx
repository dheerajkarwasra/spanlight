import { zodResolver } from "@hookform/resolvers/zod";
import { CircleAlert, Lock } from "lucide-react";
import { cloneElement, useId, type ReactElement, type ReactNode } from "react";
import { useForm } from "react-hook-form";
import { toast } from "sonner";
import { z } from "zod";

import { SectionCard } from "@/components/section-card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { useMe } from "@/features/auth";
import { errorMessage } from "@/lib/api";
import { applyServerFieldErrors } from "@/lib/form-errors";
import { formatDate } from "@/lib/format";
import { cn } from "@/lib/utils";

import { useUpdateName } from "./account-queries";
import { ChangeEmailDialog } from "./change-email-dialog";

const nameSchema = z.object({
  name: z.string().trim().min(1, "Enter your name.").max(100, "Use at most 100 characters."),
});

type NameValues = z.infer<typeof nameSchema>;

interface ProfileSectionProps {
  /** False for the shared demo account, which the server refuses to change. */
  editable: boolean;
}

/** Settings › Account › Profile (Figma "Settings — Account"): the name form and the email. */
export function ProfileSection({ editable }: ProfileSectionProps) {
  const { user } = useMe();
  const updateName = useUpdateName();
  const form = useForm<NameValues>({
    resolver: zodResolver(nameSchema),
    values: { name: user.name },
  });

  const onSubmit = form.handleSubmit((values) => {
    updateName.mutate(values.name, {
      onSuccess: () => {
        toast.success("Name saved.");
      },
      onError: (error) => {
        if (!applyServerFieldErrors(error, form.setError, ["name"])) {
          toast.error(errorMessage(error));
        }
      },
    });
  });

  return (
    <SectionCard
      title="Profile"
      description="How you appear to other members of your organizations."
    >
      <div className="flex flex-col gap-5">
        <form noValidate onSubmit={onSubmit}>
          <FieldRow
            label="Name"
            hint="Up to 100 characters."
            error={form.formState.errors.name?.message}
            control={<Input autoComplete="name" readOnly={!editable} {...form.register("name")} />}
            action={
              editable ? (
                <Button
                  type="submit"
                  size="lg"
                  disabled={!form.formState.isDirty}
                  loading={updateName.isPending}
                >
                  Save
                </Button>
              ) : null
            }
          />
        </form>
        <FieldRow
          label="Email"
          hint={
            <>
              <Lock aria-hidden className="mt-0.5 size-3 shrink-0" />
              To change it, open the link we send to the new address.
            </>
          }
          control={
            <Input
              readOnly
              value={user.email}
              className="border-border bg-surface-muted text-muted-foreground focus-visible:border-border"
            />
          }
          action={editable ? <ChangeEmailDialog /> : null}
        />
        <p className="text-xs font-medium text-subtle-foreground">
          Member since <time dateTime={user.created_at}>{formatDate(user.created_at)}</time>
        </p>
      </div>
    </SectionCard>
  );
}

interface FieldRowProps {
  label: string;
  hint: ReactNode;
  error?: string | undefined;
  control: ReactElement<{
    id?: string;
    "aria-invalid"?: boolean;
    "aria-describedby"?: string;
  }>;
  /** A button beside the input on wide screens, under the hint on phones. */
  action: ReactNode;
}

/** `FormField` with a button that lines up with the input rather than the whole field. */
function FieldRow({ label, hint, error, control, action }: FieldRowProps) {
  const id = useId();
  const noteId = `${id}-note`;
  return (
    <div className={cn("grid gap-2", action !== null && "sm:grid-cols-[1fr_auto] sm:gap-x-3")}>
      <Label htmlFor={id} className="sm:col-span-full">
        {label}
      </Label>
      {cloneElement(control, {
        id,
        "aria-invalid": error ? true : undefined,
        "aria-describedby": noteId,
      })}
      {action !== null ? <div className="max-sm:order-last max-sm:pt-1">{action}</div> : null}
      <p
        id={noteId}
        className={cn(
          "flex items-start gap-1.5 text-xs font-medium sm:col-span-full",
          error ? "text-danger-text" : "text-muted-foreground",
        )}
      >
        {error ? (
          <>
            <CircleAlert aria-hidden className="mt-px size-3.5 shrink-0" strokeWidth={2.25} />
            {error}
          </>
        ) : (
          hint
        )}
      </p>
    </div>
  );
}
