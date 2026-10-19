/** The longest text or JSON string shown before "Show all" (a 1 MiB payload stays responsive). */
export const TEXT_CLAMP_CHARS = 20_000;

/** "1,048,576 characters": what "Show all" reveals of a long text. */
export function charactersLabel(count: number): string {
  return `${count.toLocaleString("en-US")} characters`;
}
