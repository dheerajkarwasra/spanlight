/// <reference lib="dom" />
/**
 * Captures the real product screenshots shown on the landing page.
 *
 * It opens the live demo workspace the way a visitor does ("Try the live demo"), finds the
 * screens to show through the API (the latest compared experiment, an open Doctor insight, a
 * judge with a budget, an alert rule, a prompt with two versions), and writes every shot in
 * SHOTS in the light and dark theme at 1x and 2x as WebP to src/assets/landing/.
 *
 * Rerun it before each release so the landing page shows the product as it is:
 *
 *   CAPTURE_BASE_URL=https://<demo host> PW_CHANNEL=chrome node scripts/capture-landing.ts
 *
 * The demo needs real data first (see the runbook "demo-evals"): two runs of the seeded
 * experiment, an open insight from a naive lab-client run and one alert rule. The script stops
 * with an error rather than capture an empty screen.
 */
import { mkdir, writeFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";

import { chromium, type Browser, type BrowserContext, type Page } from "@playwright/test";

import {
  SHOT_IDS,
  SHOT_SCALES,
  SHOT_THEMES,
  SHOTS,
  shotBudgetBytes,
  shotFileName,
  type ShotId,
  type ShotTheme,
} from "../src/features/landing/landing-shots.ts";

const OUT_DIR = fileURLToPath(new URL("../src/assets/landing/", import.meta.url));
const VIEWPORT = { width: 1280, height: 800 } as const;
/** Kept in sync with THEME_STORAGE_KEY in src/lib/theme.ts. */
const THEME_STORAGE_KEY = "spanlight-theme";
/** WebP quality steps tried, best first, until the 2x file fits its budget. */
const QUALITIES = [0.9, 0.85, 0.8, 0.75, 0.7, 0.65, 0.6, 0.55, 0.5];

interface Workspace {
  orgId: string;
  projectId: string;
}

type Item = Record<string, unknown>;

function requireEnv(name: string): string {
  const value = process.env[name];
  if (!value) {
    throw new Error(`Set ${name}, e.g. ${name}=https://demo.example.com`);
  }
  return value.replace(/\/+$/, "");
}

/** Starts a demo session from the landing page and returns where it landed. */
async function startDemo(page: Page, baseUrl: string): Promise<Workspace> {
  await page.goto(`${baseUrl}/`);
  await page.getByRole("button", { name: "Try the live demo" }).first().click();
  await page.waitForURL(/\/[0-9a-f-]{36}\/[0-9a-f-]{36}\//, { timeout: 30_000 });
  const [orgId, projectId] = new URL(page.url()).pathname.split("/").filter(Boolean);
  if (!orgId || !projectId) {
    throw new Error(`Unexpected demo URL: ${page.url()}`);
  }
  return { orgId, projectId };
}

async function getJson(page: Page, baseUrl: string, path: string): Promise<unknown> {
  const response = await page.request.get(`${baseUrl}/api/v1${path}`);
  if (!response.ok()) {
    throw new Error(`GET ${path} answered ${response.status()}`);
  }
  return response.json();
}

async function listItems(page: Page, baseUrl: string, path: string): Promise<Item[]> {
  const body = await getJson(page, baseUrl, path);
  if (Array.isArray(body)) {
    return body as Item[];
  }
  return (body as { items: Item[] }).items;
}

function pick(items: Item[], what: string, predicate: (item: Item) => boolean): Item {
  const found = items.find(predicate);
  if (!found) {
    throw new Error(`The demo has no ${what} yet; seed it first (see the script header).`);
  }
  return found;
}

/** The in-app path of every shot, chosen from the demo's real data. */
async function resolveRoutes(
  page: Page,
  baseUrl: string,
  { orgId, projectId }: Workspace,
): Promise<Record<ShotId, string>> {
  const root = `/${orgId}/${projectId}`;
  const project = `/projects/${projectId}`;

  const experiments = await listItems(page, baseUrl, `${project}/experiments`);
  const experiment = pick(experiments, "experiment with two runs", (e) => Number(e.run_count) >= 2);
  const runs = await listItems(
    page,
    baseUrl,
    `${project}/experiments/${String(experiment.id)}/runs`,
  );
  const finished = runs.filter((run) => run.finished_at && !run.error);
  // Newest first: the latest run is the candidate, the one before it the baseline.
  const candidate = finished.at(0);
  const baseline = finished.at(1);
  if (!candidate || !baseline) {
    throw new Error("The seeded experiment needs two finished runs; run seed-evals twice.");
  }

  const insights = await listItems(page, baseUrl, `${project}/insights`);
  const insight = pick(insights, "open Doctor insight", (i) => i.status === "open");

  const prompts = await listItems(page, baseUrl, `${project}/prompts`);
  const prompt = pick(prompts, "prompt with two versions", (p) => Number(p.latest_version) >= 2);
  const latest = Number(prompt.latest_version);

  const evaluators = await listItems(page, baseUrl, `${project}/evaluators`);
  const judge = pick(evaluators, "evaluator with a budget", (e) => e.monthly_budget_usd !== null);

  const rules = await listItems(page, baseUrl, `${project}/alert-rules`);
  const rule = pick(rules, "alert rule", () => true);

  return {
    hero: `${root}/experiments/${String(experiment.id)}/compare?a=${String(baseline.id)}&b=${String(candidate.id)}`,
    doctor: `${root}/doctor/${String(insight.id)}`,
    gateway: `${root}/gateway`,
    prompts: `${root}/prompts/${encodeURIComponent(String(prompt.name))}?view=diff&a=${latest - 1}&b=${latest}`,
    evals: `${root}/evaluators/${String(judge.id)}`,
    alerts: `${root}/alerts/${String(rule.id)}`,
  };
}

async function newContext(
  browser: Browser,
  theme: ShotTheme,
  scale: number,
  storageState: Awaited<ReturnType<BrowserContext["storageState"]>>,
): Promise<BrowserContext> {
  const context = await browser.newContext({
    viewport: VIEWPORT,
    deviceScaleFactor: scale,
    colorScheme: theme,
    reducedMotion: "reduce",
    storageState,
  });
  await context.addInitScript(
    ([key, value]) => {
      window.localStorage.setItem(key, value);
    },
    [THEME_STORAGE_KEY, theme] as const,
  );
  return context;
}

/** A PNG of one shot: the whole viewport for the hero, a crop from the page title otherwise. */
async function capturePng(page: Page, baseUrl: string, id: ShotId, path: string): Promise<Buffer> {
  await page.goto(`${baseUrl}${path}`);
  await page.waitForLoadState("networkidle");
  // Skeletons (the shimmer class) are replaced by data once every query has settled.
  await page.waitForFunction(() => !document.querySelector(".animate-shimmer"), undefined, {
    timeout: 15_000,
  });
  await page.addStyleTag({ content: "[data-sonner-toaster]{display:none!important}" });

  if (id === "hero") {
    return page.screenshot();
  }
  const { width, height } = SHOTS[id];
  const title = await page.locator("main h1").first().boundingBox();
  const main = await page.locator("main").first().boundingBox();
  if (!title || !main) {
    throw new Error(`No page title on ${path}`);
  }
  const x = Math.max(main.x, title.x - 16);
  const y = Math.max(main.y, title.y - 16);
  return page.screenshot({ clip: { x, y, width, height } });
}

/** Encodes a PNG as WebP in the browser (Chrome's encoder), so no image tooling is needed. */
async function toWebp(page: Page, png: Buffer, quality: number): Promise<Buffer> {
  const base64 = await page.evaluate(
    async ({ data, q }) => {
      const bytes = Uint8Array.from(atob(data), (char) => char.charCodeAt(0));
      const bitmap = await createImageBitmap(new Blob([bytes], { type: "image/png" }));
      const canvas = new OffscreenCanvas(bitmap.width, bitmap.height);
      const context = canvas.getContext("2d");
      if (!context) {
        throw new Error("No 2D canvas");
      }
      context.drawImage(bitmap, 0, 0);
      const blob = await canvas.convertToBlob({ type: "image/webp", quality: q });
      const buffer = new Uint8Array(await blob.arrayBuffer());
      let binary = "";
      for (const byte of buffer) {
        binary += String.fromCharCode(byte);
      }
      return btoa(binary);
    },
    { data: png.toString("base64"), q: quality },
  );
  return Buffer.from(base64, "base64");
}

/** The best quality at which the 2x capture fits the shot's budget. */
async function fitQuality(encoder: Page, id: ShotId, png2x: Buffer): Promise<number> {
  for (const quality of QUALITIES) {
    const webp = await toWebp(encoder, png2x, quality);
    if (webp.length <= shotBudgetBytes(id)) {
      return quality;
    }
  }
  throw new Error(`${id} does not fit ${shotBudgetBytes(id)} bytes even at the lowest quality`);
}

async function main(): Promise<void> {
  const baseUrl = requireEnv("CAPTURE_BASE_URL");
  const browser = await chromium.launch({ channel: process.env.PW_CHANNEL });
  try {
    const setup = await browser.newContext({ viewport: VIEWPORT });
    const setupPage = await setup.newPage();
    const workspace = await startDemo(setupPage, baseUrl);
    const routes = await resolveRoutes(setupPage, baseUrl, workspace);
    const storageState = await setup.storageState();
    const encoder = await setup.newPage();

    await mkdir(OUT_DIR, { recursive: true });
    for (const theme of SHOT_THEMES) {
      const pngs = new Map<string, Buffer>();
      for (const scale of SHOT_SCALES) {
        const context = await newContext(browser, theme, scale, storageState);
        const page = await context.newPage();
        for (const id of SHOT_IDS) {
          pngs.set(shotFileName(id, theme, scale), await capturePng(page, baseUrl, id, routes[id]));
        }
        await context.close();
      }
      for (const id of SHOT_IDS) {
        const png2x = pngs.get(shotFileName(id, theme, 2));
        const png1x = pngs.get(shotFileName(id, theme, 1));
        if (!png1x || !png2x) {
          throw new Error(`Missing capture for ${id} (${theme})`);
        }
        const quality = await fitQuality(encoder, id, png2x);
        for (const [scale, png] of [
          [1, png1x],
          [2, png2x],
        ] as const) {
          const name = shotFileName(id, theme, scale);
          const webp = await toWebp(encoder, png, quality);
          await writeFile(`${OUT_DIR}${name}`, webp);
          console.log(`${name}  ${Math.round(webp.length / 1024)} KB  q=${quality}`);
        }
      }
    }
    await setup.close();
  } finally {
    await browser.close();
  }
}

await main();
