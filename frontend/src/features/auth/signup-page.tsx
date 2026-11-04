import { getRouteApi, Link } from "@tanstack/react-router";
import { CirclePlay } from "lucide-react";

import { Button } from "@/components/ui/button";

import { AuthFooter } from "./auth-footer";
import { AuthLayout } from "./auth-layout";
import { OAuthButtons } from "./oauth-buttons";
import { SignupForm } from "./signup-form";
import { TermsLine } from "./terms-line";
import { useDemoSession } from "./use-demo-session";

const routeApi = getRouteApi("/signup");

export function SignupPage() {
  const { next } = routeApi.useSearch();
  const demo = useDemoSession();

  return (
    <AuthLayout
      title="Create an"
      accent="account."
      description="Start sending traces in a couple of minutes."
    >
      <OAuthButtons next={next} dividerLabel="or sign up with email" note={<TermsLine />} />
      <SignupForm next={next} />

      <Button
        variant="ghost"
        className="self-center"
        loading={demo.isPending}
        onClick={() => {
          demo.mutate();
        }}
      >
        {demo.isPending ? null : <CirclePlay aria-hidden />}
        Or try the live demo first
      </Button>

      <AuthFooter>
        Already have an account?
        <Link to="/login" search={{ next }} className="font-semibold text-accent hover:underline">
          Sign in
        </Link>
      </AuthFooter>
    </AuthLayout>
  );
}
