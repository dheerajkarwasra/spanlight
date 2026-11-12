import { Figure, Section, SectionHeading, Wires } from "./landing-parts";
import { stagger } from "./motion";
import { SECTION_IDS } from "./links";

const GATEWAY_STEPS = [
  "exact-match cache",
  "per-key rate limit",
  "budget check",
  "retry, then fallback",
] as const;

/** Provider nodes of the desktop diagram, top to bottom, with their Figma offsets. */
const PROVIDERS = [
  { name: "OpenAI", note: null, top: 57 },
  { name: "Anthropic", note: "fallback route", top: 147 },
  { name: "Gemini", note: "OpenAI-compatible", top: 237 },
  { name: "Bedrock", note: "OpenAI-compatible", top: 327 },
] as const;

/** Wire paths from the Figma vectors, each in a 201.5 × 440 box. */
const REQUEST_WIRE = ["M0.75 220H200.75"] as const;
const PROVIDER_WIRES = [
  "M0.75 190C100.75 190 100.75 92 200.75 92",
  "M0.75 210C100.75 210 100.75 182 200.75 182",
  "M0.75 230C100.75 230 100.75 272 200.75 272",
  "M0.75 250C100.75 250 100.75 362 200.75 362",
] as const;

const CAPABILITIES = [
  { title: "Sealed credentials", text: "Provider keys stay sealed on the server." },
  { title: "Retries and fallbacks", text: "Routes retry a failed call, then fall back." },
  { title: "Per-key rate limits", text: "Each gateway key carries its own limit." },
  { title: "Exact-match cache", text: "An identical request is answered from cache." },
  {
    title: "Integration Lab",
    chip: "Integration Lab: real provider failures on test keys",
    text: "Injects real provider failures on non-production keys.",
  },
] as const;

const DIAGRAM_ALT =
  "Your app, using the official SDK unchanged, sends requests through base_url to the Spanlight gateway, which traces every request and applies an exact-match cache, a per-key rate limit, a budget check and retry then fallback, before routing to OpenAI, Anthropic as the fallback route, or Gemini and Bedrock through their OpenAI-compatible endpoints.";

const WIRE = "var(--mono-line-strong)";
const PULSE = "var(--mono-900)";
const NODE =
  "flex flex-col justify-center gap-[3px] rounded-[10px] border border-mono-line-strong bg-mono-white px-5";
const NODE_TITLE = "leading-[1.3] font-medium tracking-[-0.015em] text-mono-900";
const NODE_NOTE = "font-mono text-xs leading-[1.4] text-mono-500";

/** "Route": the gateway diagram and what the gateway adds (Figma "Route"). */
export function RouteSection() {
  return (
    <Section id={SECTION_IDS.route} tone="white">
      <SectionHeading
        id={SECTION_IDS.route}
        label="Route"
        title="Change one base URL."
        description="The official OpenAI and Anthropic SDKs work against the gateway unchanged, streaming and tool calls included."
      />

      {/* Desktop: requests run the wires; the four provider wires pulse in turn. */}
      <Figure
        label={DIAGRAM_ALT}
        className="relative hidden h-[440px] w-[1200px] overflow-clip rounded-2xl border border-mono-line bg-mono-paper xl:block"
      >
        <Wires
          paths={REQUEST_WIRE}
          width={201.5}
          height={440}
          stroke={WIRE}
          pulse={PULSE}
          className="-top-px left-[258.25px]"
        />
        <Wires
          paths={PROVIDER_WIRES}
          width={201.5}
          height={440}
          stroke={WIRE}
          pulse={PULSE}
          turns
          pulseDelay={1200}
          className="-top-px left-[738.25px]"
        />
        <div className={`${NODE} absolute top-[179px] left-[59px] h-20 w-[200px]`}>
          <span className={`${NODE_TITLE} text-base`}>Your app</span>
          <span className={NODE_NOTE}>official SDK, unchanged</span>
        </div>
        <span className={`${NODE_NOTE} absolute top-[195px] left-[273px]`}>base_url</span>
        <div className="absolute top-[79px] left-[459px] flex size-[280px] flex-col gap-1.5 rounded-[10px] bg-mono-black px-5 pt-[26px] pb-6">
          <span className="text-[22px] leading-[1.3] font-medium tracking-[-0.015em] text-mono-white">
            Spanlight gateway
          </span>
          <span className="font-mono text-xs leading-[1.4] text-mono-400">
            every request traced
          </span>
          <div className="flex flex-col gap-2 pt-[18px]">
            {GATEWAY_STEPS.map((step, index) => (
              <span
                key={step}
                style={stagger(index + 1)}
                className="lp-rise flex items-center gap-2 rounded-md border border-mono-800 px-3 py-2 font-mono text-xs leading-[1.4] text-mono-white [--lp-rise:8px]"
              >
                <span className="size-1.5 rounded-full bg-mono-white" />
                {step}
              </span>
            ))}
          </div>
        </div>
        {PROVIDERS.map((provider) => (
          <div
            key={provider.name}
            style={{ top: provider.top }}
            className={`${NODE} absolute left-[939px] h-[68px] w-[200px]`}
          >
            <span className={`${NODE_TITLE} text-base`}>{provider.name}</span>
            {provider.note && <span className={NODE_NOTE}>{provider.note}</span>}
          </div>
        ))}
      </Figure>

      {/* Phones: the same route, stacked and still. */}
      <Figure
        label={DIAGRAM_ALT}
        className="flex w-full flex-col rounded-2xl border border-mono-line bg-mono-paper p-5 xl:hidden"
      >
        <div className={`${NODE} lp-rise h-[68px]`}>
          <span className={`${NODE_TITLE} text-[15px]`}>Your app</span>
          <span className={NODE_NOTE}>official SDK, unchanged</span>
        </div>
        <div className="ml-[112px] flex h-9 items-center gap-3 border-l border-mono-line-strong pl-3">
          <span className={NODE_NOTE}>base_url</span>
        </div>
        <div
          style={stagger(1)}
          className="lp-rise flex h-[66px] flex-col justify-center gap-[3px] rounded-[10px] bg-mono-black px-4"
        >
          <span className="text-[15px] leading-[1.3] font-medium tracking-[-0.015em] text-mono-white">
            Spanlight gateway
          </span>
          <span className="font-mono text-xs leading-[1.4] text-mono-400">
            every request traced
          </span>
        </div>
        <div className="ml-14 flex h-9 items-center gap-3 border-l border-mono-line-strong pl-3">
          <span className={NODE_NOTE}>route · retry · fallback</span>
        </div>
        <div style={stagger(2)} className="lp-rise flex gap-2.5">
          {PROVIDERS.slice(0, 2).map((provider) => (
            <div key={provider.name} className={`${NODE} h-12 flex-1`}>
              <span className={`${NODE_TITLE} text-[15px]`}>{provider.name}</span>
            </div>
          ))}
        </div>
      </Figure>

      <ul className="hidden flex-wrap justify-center gap-2.5 xl:flex">
        {CAPABILITIES.map((capability) => (
          <li
            key={capability.title}
            className="rounded-full border border-mono-line-strong px-3.5 py-[9px] text-sm leading-[1.3] font-medium text-mono-900"
          >
            {"chip" in capability ? capability.chip : capability.title}
          </li>
        ))}
      </ul>

      <div className="flex flex-col gap-7 xl:hidden">
        <ul className="flex flex-col">
          {CAPABILITIES.map((capability) => (
            <li
              key={capability.title}
              className="flex flex-col gap-1 border-t border-mono-line-strong py-4"
            >
              <h3 className="text-[15px] leading-[1.4] font-medium text-mono-900">
                {capability.title}
              </h3>
              <p className="text-sm leading-[1.5] text-mono-600">{capability.text}</p>
            </li>
          ))}
        </ul>
        <p className="text-xs leading-[1.5] text-mono-500">
          Gemini and Bedrock work through their OpenAI-compatible endpoints. Their cost stays
          unknown until you add a price for their models.
        </p>
      </div>
    </Section>
  );
}
