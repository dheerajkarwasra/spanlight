import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen } from "@testing-library/react";
import type { ReactElement, ReactNode } from "react";
import { describe, expect, it, vi } from "vitest";

import { CodeInput } from "./code-input";
import { LoginForm } from "./login-form";
import { ResetPasswordForm } from "./reset-password-form";
import { SignupForm } from "./signup-form";

vi.mock("@tanstack/react-router", () => ({
  Link: ({ children }: { children: ReactNode }) => <a href="/forgot-password">{children}</a>,
  useNavigate: () => vi.fn(),
}));

function renderForm(form: ReactElement) {
  const { container } = render(
    <QueryClientProvider client={new QueryClient()}>{form}</QueryClientProvider>,
  );
  return Array.from(container.querySelectorAll<HTMLInputElement>('input[type="password"]'));
}

// WCAG 3.3.8: a password manager or a paste must be able to fill every password field.
describe("password fields", () => {
  it.each([
    ["sign-in", <LoginForm key="login" next={undefined} />, ["current-password"]],
    ["sign-up", <SignupForm key="signup" next={undefined} />, ["new-password"]],
    [
      "reset",
      <ResetPasswordForm key="reset" token="t" onInvalidLink={() => undefined} />,
      ["new-password", "new-password"],
    ],
  ])("%s: names the autocomplete purpose and never blocks paste", (_, form, purposes) => {
    const inputs = renderForm(form);
    expect(inputs.map((input) => input.autocomplete)).toEqual(purposes);
    for (const input of inputs) {
      const allowed = fireEvent.paste(input, { clipboardData: { getData: () => "hunter2-long" } });
      expect(allowed).toBe(true);
    }
  });

  it("accepts a pasted two-factor code with its space", () => {
    const onChange = vi.fn();
    render(
      <CodeInput
        value=""
        onChange={onChange}
        invalid={false}
        input={{ ref: null, "aria-labelledby": undefined }}
      />,
    );
    const input = screen.getByRole("textbox");
    expect(input).toHaveAttribute("autocomplete", "one-time-code");
    fireEvent.paste(input, { clipboardData: { getData: () => "123 456" } });
    expect(onChange).toHaveBeenCalledWith("123456");
  });
});
