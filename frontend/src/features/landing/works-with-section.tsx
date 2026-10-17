/** SDKs and the OTLP conventions the ingest path reads (CHANGELOG 0.6.0, OTLP on-ramps). */
const STACK = [
  "OpenAI",
  "Anthropic",
  "OpenTelemetry",
  "OpenInference",
  "Vercel AI SDK",
  "OpenLLMetry",
  "LiteLLM",
] as const;

/** Text wordmarks of what Spanlight reads, as written (no logos). */
export function WorksWithSection() {
  return (
    <section
      aria-labelledby="works-with-title"
      className="flex flex-col items-center gap-[18px] px-5 pt-10 text-subtle-foreground md:px-8 lg:gap-7 lg:pt-14"
    >
      <h2 id="works-with-title" className="text-overline uppercase">
        Reads what your stack already sends
      </h2>
      <ul className="flex w-full max-w-[1200px] flex-wrap items-center justify-center gap-x-[22px] gap-y-3 text-[18px] leading-[1.25] font-extrabold tracking-[-0.02em] lg:justify-between lg:px-12 lg:text-2xl lg:leading-[1.25]">
        {STACK.map((name) => (
          <li key={name}>{name}</li>
        ))}
      </ul>
    </section>
  );
}
