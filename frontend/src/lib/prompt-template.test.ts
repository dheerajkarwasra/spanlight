import { describe, expect, it } from "vitest";

import fixture from "@/features/prompts/template-cases.json";

import {
  InvalidTemplateError,
  MAX_COMPILED_BYTES,
  MAX_TEMPLATE_BYTES,
  MissingVariablesError,
  TEMPLATE_TOO_LARGE_MESSAGE,
  TemplateTooLargeError,
  compileTemplate,
  describeCompileError,
  extractVariables,
  highlightParts,
  inspectTemplate,
  type LooseTemplate,
} from "./prompt-template";

interface FixtureCase {
  name: string;
  template: LooseTemplate;
  variables: Record<string, string>;
  expect: {
    text?: string;
    messages?: { role: string; content: string }[];
    variables_used?: string[];
    unused_variables?: string[];
    error?: string;
    names?: string[];
  };
}

const cases = (fixture as { cases: FixtureCase[] }).cases;

// The same fixture drives the server (`prompts/templates.py`) and the Python SDK, so a preview in
// the browser must render exactly what the server stores and the SDK sends.
describe("compileTemplate matches the shared server/SDK fixture", () => {
  it("has cases to check", () => {
    expect(cases.length).toBeGreaterThan(30);
  });

  it.each(cases.map((testCase) => [testCase.name, testCase] as const))("%s", (_, testCase) => {
    const run = () => compileTemplate(testCase.template, testCase.variables);
    const expected = testCase.expect;
    if (expected.error === "missing_variables") {
      expect(run).toThrow(MissingVariablesError);
      try {
        run();
      } catch (error) {
        expect((error as MissingVariablesError).names).toEqual(expected.names);
      }
      return;
    }
    if (expected.error === "invalid_template") {
      expect(run).toThrow(InvalidTemplateError);
      return;
    }
    expect(expected.error).toBeUndefined();
    const compiled = run();
    expect(compiled.text).toEqual(expected.text ?? null);
    expect(compiled.messages).toEqual(expected.messages ?? null);
    expect(compiled.variablesUsed).toEqual(expected.variables_used);
    expect(compiled.unusedVariables).toEqual(expected.unused_variables);
  });
});

describe("template size limits (UTF-8 bytes, as the server counts them)", () => {
  it("accepts a template of exactly 64 KB and rejects one byte more", () => {
    const exact = "a".repeat(MAX_TEMPLATE_BYTES);
    expect(extractVariables({ kind: "text", text: exact })).toEqual([]);
    const over = { kind: "text", text: `${exact}a` };
    expect(() => extractVariables(over)).toThrow(InvalidTemplateError);
  });

  it("counts multi-byte characters by their encoded size, not string length", () => {
    // 3 bytes each in UTF-8: 21,846 characters are 65,538 bytes, past the 64 KB cap.
    const text = "€".repeat(Math.ceil(MAX_TEMPLATE_BYTES / 3) + 1);
    expect(text.length).toBeLessThan(MAX_TEMPLATE_BYTES);
    expect(inspectTemplate({ kind: "text", text }).problem?.reason).toBe("too-large");
  });

  it("sums every chat message toward the template cap", () => {
    const half = "a".repeat(MAX_TEMPLATE_BYTES / 2 + 1);
    const template = {
      kind: "chat",
      messages: [
        { role: "system", content: half },
        { role: "user", content: half },
      ],
    };
    expect(inspectTemplate(template).problem?.reason).toBe("too-large");
  });

  it("rejects a chat prompt with more than 50 messages", () => {
    const messages = Array.from({ length: 51 }, () => ({ role: "user", content: "x" }));
    expect(inspectTemplate({ kind: "chat", messages }).problem?.reason).toBe("shape");
  });

  it("refuses to render a small template that repeats a large value past 256 KB", () => {
    const value = "a".repeat(MAX_COMPILED_BYTES / 4);
    const text = "{{v}}{{v}}{{v}}{{v}}x";
    expect(() => compileTemplate({ kind: "text", text }, { v: value })).toThrow(
      TemplateTooLargeError,
    );
    expect(
      compileTemplate({ kind: "text", text: "{{v}}{{v}}{{v}}{{v}}" }, { v: value }).text,
    ).toHaveLength(MAX_COMPILED_BYTES);
  });

  it("rejects an unknown template kind instead of guessing", () => {
    expect(inspectTemplate({ kind: "image", text: "x" }).problem?.reason).toBe("shape");
  });
});

describe("variable names that collide with Object.prototype", () => {
  it("treats a missing `constructor` variable as missing, not as the inherited function", () => {
    expect(() => compileTemplate({ kind: "text", text: "{{constructor}}" }, {})).toThrow(
      MissingVariablesError,
    );
  });

  it("renders a supplied `__proto__` variable verbatim", () => {
    const variables = Object.fromEntries([["__proto__", "value"]]) as Record<string, string>;
    expect(compileTemplate({ kind: "text", text: "<{{__proto__}}>" }, variables).text).toBe(
      "<value>",
    );
  });
});

describe("error positions and sentences the editor shows", () => {
  it("points at the line and column of an unclosed brace", () => {
    const { problem } = inspectTemplate({ kind: "text", text: "ok\n  {{name" });
    expect(problem?.reason).toBe("unclosed");
    expect(problem?.position).toEqual({ message: null, line: 2, column: 3 });
    expect(describeCompileError(problem)).toBe("Line 2: “{{” has no closing “}}”.");
  });

  it("names the message (1-based) for a chat template", () => {
    const { problem } = inspectTemplate({
      kind: "chat",
      messages: [
        { role: "system", content: "fine" },
        { role: "user", content: "{{bad-name}}" },
      ],
    });
    expect(problem?.position).toEqual({ message: 1, line: 1, column: 1 });
    expect(describeCompileError(problem)).toMatch(/^Message 2, line 1: “\{\{bad-name\}\}”/);
  });

  it("lists every missing variable, sorted, in one sentence", () => {
    const run = () => compileTemplate({ kind: "text", text: "{{b}} {{a}}" }, {});
    expect(() => run()).toThrow(MissingVariablesError);
    try {
      run();
    } catch (error) {
      expect(describeCompileError(error)).toBe("Fill in the variables a, b.");
    }
  });

  it("uses the 256 KB sentence for an over-large rendered prompt", () => {
    expect(describeCompileError(new TemplateTooLargeError())).toBe(TEMPLATE_TOO_LARGE_MESSAGE);
  });

  it("returns null for an error that is not a compile error", () => {
    expect(describeCompileError(new Error("network"))).toBeNull();
  });
});

describe("highlightParts", () => {
  it("marks only well-formed references and keeps all text", () => {
    const text = "Hi {{ name }}, {{1bad}} and {{ok_2}}!";
    const parts = highlightParts(text);
    expect(parts.map((part) => part.text).join("")).toBe(text);
    expect(parts.filter((part) => part.variable).map((part) => part.text)).toEqual([
      "{{ name }}",
      "{{ok_2}}",
    ]);
  });
});
