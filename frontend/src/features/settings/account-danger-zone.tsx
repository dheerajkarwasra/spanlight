import { Link } from "@tanstack/react-router";
import { useEffect, useRef, useState } from "react";

import { Callout } from "@/components/callout";
import { DialogHeading } from "@/components/dialog-heading";
import { DisabledReason } from "@/components/disabled-reason";
import { FormField } from "@/components/form-field";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogFooter, DialogTrigger } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { useMe } from "@/features/auth";
import { useOrgProjectsQuery } from "@/features/shell";
import { errorMessage, isApiError, type Org } from "@/lib/api";

import { isReauthRequired, useDeleteAccount, useSoleOwnerOrgs } from "./account-queries";
import { AuthenticationCodeField, SignInAgainCallout } from "./change-email-dialog";
import { isConfirmationMismatch } from "./confirm-slug";
import { DangerZoneCard } from "./danger-zone-card";

const EXPLANATION =
  "This permanently deletes your account, its sessions, tokens and sign-in methods. Your organizations stay with their other members. This can’t be undone.";

const CONFIRM_WORD = "delete";

/** What `409 SOLE_OWNER` names, and what the blocked state lists. */
type OwnedOrg = Pick<Org, "id" | "slug" | "name">;

/** The `orgs` of a `409 SOLE_OWNER`, or null when the error is another one or carries none. */
function soleOwnerOrgsOf(error: unknown): OwnedOrg[] | null {
  if (!isApiError(error) || error.code !== "SOLE_OWNER") {
    return null;
  }
  const { orgs } = error.extensions;
  const valid =
    Array.isArray(orgs) &&
    orgs.length > 0 &&
    orgs.every(
      (org: unknown): org is OwnedOrg =>
        typeof org === "object" &&
        org !== null &&
        ["id", "slug", "name"].every(
          (key) => typeof (org as Record<string, unknown>)[key] === "string",
        ),
    );
  return valid ? orgs : null;
}

const DELETED = [
  "Your profile: name, email address and password",
  "Sessions, personal access tokens and GitHub or Google sign-ins",
  "Two-factor settings and recovery codes",
  "Your memberships. Organizations and their projects stay with their other members.",
  "Read access on API keys you created. Read-only keys are revoked; ingest keys keep sending traces.",
];

/** Settings › Account › Danger zone (Figma "Settings — Account"): delete the account. */
export function AccountDangerZone() {
  const [open, setOpen] = useState(false);
  const [pending, setPending] = useState(false);

  return (
    <DangerZoneCard label="Delete account" explanation={EXPLANATION}>
      <Dialog
        open={open}
        onOpenChange={(next) => {
          if (!pending) {
            setOpen(next);
          }
        }}
      >
        <DialogTrigger asChild>
          <Button variant="danger">Delete account</Button>
        </DialogTrigger>
        {/* Mounted only while open, so the fields start empty and the owner check runs fresh. */}
        {open ? (
          <DeleteAccountDialogContent
            pending={pending}
            setPending={setPending}
            close={() => {
              setOpen(false);
            }}
          />
        ) : null}
      </Dialog>
    </DangerZoneCard>
  );
}

interface DeleteAccountDialogContentProps {
  pending: boolean;
  setPending: (pending: boolean) => void;
  close: () => void;
}

/**
 * The delete dialog (Figma "Delete account"), in the typed-slug dialog's look: what goes, the
 * password (or the sign-in-again note), the two-factor code when it is on, and the word `delete`. While the caller is the only owner of
 * an organization it lists those instead, and the delete button stays off.
 */
function DeleteAccountDialogContent({
  pending,
  setPending,
  close,
}: DeleteAccountDialogContentProps) {
  const me = useMe();
  const precheckOrgs = useSoleOwnerOrgs(me);
  // The server's answer wins over the pre-check once there is one.
  const [serverOrgs, setServerOrgs] = useState<OwnedOrg[] | null>(null);
  const soleOwnerOrgs: readonly OwnedOrg[] | undefined = serverOrgs ?? precheckOrgs;
  const deleteAccount = useDeleteAccount();
  const [password, setPassword] = useState("");
  const [code, setCode] = useState("");
  const [codeError, setCodeError] = useState<string | null>(null);
  const [typed, setTyped] = useState("");
  const [passwordError, setPasswordError] = useState<string | null>(null);
  const [confirmError, setConfirmError] = useState<string | null>(null);
  const [reauth, setReauth] = useState(false);
  // Shown in the dialog: a toast would sit outside the modal, hidden from screen readers.
  const [formError, setFormError] = useState<string | null>(null);
  const contentRef = useRef<HTMLDivElement>(null);
  const passwordRef = useRef<HTMLInputElement>(null);
  const codeRef = useRef<HTMLInputElement>(null);
  const confirmRef = useRef<HTMLInputElement>(null);

  const needsPassword = me.has_password;
  const needsCode = me.totp_enabled;
  const blocked = soleOwnerOrgs !== undefined && soleOwnerOrgs.length > 0;
  const showsFields = soleOwnerOrgs !== undefined && !blocked;

  // The fields appear once the owner check answers. If focus is still where opening the dialog put
  // it (the dialog itself or its close button), move it to the first field.
  useEffect(() => {
    const content = contentRef.current;
    const active = document.activeElement;
    if (!showsFields || !content || !active) {
      return;
    }
    if (
      active === content ||
      (content.contains(active) && active.matches('[aria-label="Close"]'))
    ) {
      (passwordRef.current ?? codeRef.current ?? confirmRef.current)?.focus();
    }
  }, [showsFields]);
  const ready =
    soleOwnerOrgs !== undefined &&
    !blocked &&
    !reauth &&
    typed === CONFIRM_WORD &&
    (!needsPassword || password !== "") &&
    (!needsCode || code.trim() !== "");

  function submit() {
    if (!ready || pending) {
      return;
    }
    setPending(true);
    setFormError(null);
    deleteAccount.mutate(
      {
        confirm: typed,
        ...(needsPassword ? { password } : {}),
        ...(needsCode ? { code: code.trim() } : {}),
      },
      {
        onError: (error) => {
          setPending(false);
          const owned = soleOwnerOrgsOf(error);
          if (owned) {
            setServerOrgs(owned);
          } else if (isReauthRequired(error)) {
            setReauth(true);
          } else if (isApiError(error) && error.code === "INVALID_PASSWORD") {
            setPasswordError(error.message);
            passwordRef.current?.focus();
          } else if (
            isApiError(error) &&
            (error.code === "TOTP_CODE_REQUIRED" || error.code === "INVALID_TOTP_CODE")
          ) {
            setCodeError(error.message);
            codeRef.current?.focus();
          } else if (isConfirmationMismatch(error)) {
            setConfirmError(`That doesn't match ${CONFIRM_WORD}.`);
          } else {
            setFormError(errorMessage(error));
          }
        },
        onSuccess: () => {
          setPending(false);
          close();
        },
      },
    );
  }

  const deleteButton = (
    <Button
      type="submit"
      variant="danger"
      disabled={!ready}
      loading={pending}
      className="max-sm:w-full"
    >
      Delete account
    </Button>
  );
  const disabledReason = blocked
    ? "Hand over or delete the organizations listed first."
    : reauth
      ? "Sign in again first."
      : soleOwnerOrgs === undefined
        ? "Checking your organizations."
        : `Enter ${needsPassword ? "your password and " : ""}${
            needsCode ? "your authentication code and " : ""
          }type ${CONFIRM_WORD} to confirm.`;

  return (
    <DialogContent
      ref={contentRef}
      hideClose
      className="max-w-[560px] gap-5 rounded-card p-5 sm:p-7"
      onOpenAutoFocus={(event) => {
        // The first field, not the close button. While the owner check loads, or when it blocks,
        // there is no field yet: the dialog's own default (its first control) applies.
        const field = passwordRef.current ?? codeRef.current ?? confirmRef.current;
        if (field) {
          event.preventDefault();
          field.focus();
        }
      }}
    >
      <DialogHeading
        title="Delete account"
        description={
          blocked
            ? "You’re the only owner of these organizations. Make another member an owner, or delete the organization. Then you can delete your account."
            : "This permanently deletes your account. This can’t be undone."
        }
        showClose={pending ? "disabled" : true}
      />
      {soleOwnerOrgs === undefined ? (
        <Skeleton
          role="status"
          aria-label="Checking your organizations"
          className="h-[129px] rounded-tile"
        />
      ) : blocked ? (
        <SoleOwnerTile orgs={soleOwnerOrgs} />
      ) : (
        <>
          <div className="flex flex-col gap-2 rounded-tile bg-surface-muted px-4 py-3.5">
            <p className="text-overline text-subtle-foreground uppercase">What is deleted</p>
            <ul className="flex flex-col gap-1.5">
              {DELETED.map((item) => (
                <li key={item} className="flex gap-2.5 text-sm font-medium">
                  <span
                    aria-hidden
                    className="mt-2 size-1.5 shrink-0 rounded-full bg-muted-foreground"
                  />
                  {item}
                </li>
              ))}
            </ul>
          </div>
          <p className="text-xs font-medium text-muted-foreground">
            Audit records keep your email for up to 365 days; backups age out within about 8 weeks.
          </p>
        </>
      )}
      <form
        noValidate
        className="flex flex-col gap-5"
        onSubmit={(event) => {
          event.preventDefault();
          submit();
        }}
      >
        {showsFields ? (
          <>
            {reauth ? (
              <SignInAgainCallout action="Deleting your account" />
            ) : needsPassword ? (
              <FormField label="Password" error={passwordError ?? undefined}>
                <Input
                  ref={passwordRef}
                  type="password"
                  autoComplete="current-password"
                  value={password}
                  readOnly={pending}
                  onChange={(event) => {
                    setPassword(event.target.value);
                    setPasswordError(null);
                  }}
                />
              </FormField>
            ) : null}
            {needsCode && !reauth ? (
              <AuthenticationCodeField
                ref={codeRef}
                value={code}
                readOnly={pending}
                error={codeError ?? undefined}
                onChange={(event) => {
                  setCode(event.target.value);
                  setCodeError(null);
                }}
              />
            ) : null}
            <FormField
              label={
                <>
                  Type <code className="font-mono font-normal">{CONFIRM_WORD}</code> to confirm
                </>
              }
              error={confirmError ?? undefined}
            >
              <Input
                ref={confirmRef}
                value={typed}
                onChange={(event) => {
                  setTyped(event.target.value);
                  setConfirmError(null);
                }}
                autoComplete="off"
                autoCapitalize="none"
                autoCorrect="off"
                spellCheck={false}
                readOnly={pending}
                className="font-mono text-label"
              />
            </FormField>
            {/* The visible errors are tied to their fields, but a new one is not announced. */}
            <p role="status" className="sr-only">
              {passwordError ?? codeError ?? confirmError ?? ""}
            </p>
          </>
        ) : null}
        {/* Each submit clears it; hidden in the blocked and sign-in-again states, which say why. */}
        {formError && showsFields && !reauth ? (
          <Callout tone="danger" role="alert">
            {formError}
          </Callout>
        ) : null}
        <DialogFooter className="gap-2.5">
          <Button onClick={close} disabled={pending}>
            {blocked ? "Close" : "Cancel"}
          </Button>
          {ready ? (
            deleteButton
          ) : (
            <DisabledReason reason={disabledReason}>{deleteButton}</DisabledReason>
          )}
        </DialogFooter>
      </form>
    </DialogContent>
  );
}

/** "2 ORGANIZATIONS": the ones the caller is the only owner of, each with where to fix it. */
function SoleOwnerTile({ orgs }: { orgs: readonly OwnedOrg[] }) {
  return (
    <div className="flex flex-col gap-2 rounded-tile bg-surface-muted px-4 py-3.5">
      <p className="text-overline text-subtle-foreground uppercase">
        {orgs.length} {orgs.length === 1 ? "organization" : "organizations"}
      </p>
      <ul aria-label="Organizations you are the only owner of" className="flex flex-col gap-2">
        {orgs.map((org) => (
          <SoleOwnerRow key={org.id} org={org} />
        ))}
      </ul>
    </div>
  );
}

const ORG_LINK_CLASS =
  "rounded-xs py-0.5 font-medium text-accent underline underline-offset-2 hover:text-accent-hover";

function SoleOwnerRow({ org }: { org: OwnedOrg }) {
  // Organization pages live under a project; any of the org's projects will do.
  const project = useOrgProjectsQuery(org.id).data?.[0];
  const params = project ? { orgId: org.id, projectId: project.id } : null;

  return (
    <li className="flex flex-col gap-1 text-sm sm:flex-row sm:items-center sm:justify-between sm:gap-4">
      <span className="min-w-0 font-medium [overflow-wrap:anywhere]">{org.name}</span>
      {params ? (
        <span className="flex shrink-0 gap-3">
          <Link
            to="/$orgId/$projectId/settings/members"
            params={params}
            aria-label={`${org.name} members`}
            className={ORG_LINK_CLASS}
          >
            Members
          </Link>
          <Link
            to="/$orgId/$projectId/settings/organization"
            params={params}
            aria-label={`${org.name} organization settings`}
            className={ORG_LINK_CLASS}
          >
            Organization settings
          </Link>
        </span>
      ) : null}
    </li>
  );
}
