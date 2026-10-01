// Renders card graphics to PNG.
//
// Usage: node scripts/render_graphics.mjs <site-dir>
// Reads <site-dir>/_graphics/manifest.json ([{html, png}], paths relative to
// <site-dir>), screenshots each HTML card at its declared size and writes the PNG,
// then removes <site-dir>/_graphics. Cards are described in scripts/visuals.py;
// build_site.py writes the manifest.
//
// Playwright comes from the PLAYWRIGHT_MODULE env var, a local install, or the
// global npm root, in that order.

import { createRequire } from "node:module";
import { execSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";

const require = createRequire(import.meta.url);
function loadPlaywright() {
  const tries = [process.env.PLAYWRIGHT_MODULE, "playwright"];
  try { tries.push(path.join(execSync("npm root -g").toString().trim(), "playwright")); } catch (e) {}
  for (const t of tries) {
    if (!t) continue;
    try { return require(t); } catch (e) {}
  }
  throw new Error("playwright not found");
}

const site = path.resolve(process.argv[2] || "_site");
const dir = path.join(site, "_graphics");
const manifestPath = path.join(dir, "manifest.json");
if (!fs.existsSync(manifestPath)) {
  console.log("render_graphics: no manifest, nothing to render");
  process.exit(0);
}
const jobs = JSON.parse(fs.readFileSync(manifestPath, "utf8"));
const { chromium } = loadPlaywright();
const browser = await chromium.launch(process.env.CHROMIUM_PATH ? { executablePath: process.env.CHROMIUM_PATH } : {});
const page = await browser.newPage({ viewport: { width: 1200, height: 630 }, deviceScaleFactor: 1 });
let done = 0;
for (const job of jobs) {
  const out = path.join(site, job.png);
  fs.mkdirSync(path.dirname(out), { recursive: true });
  // Each card declares its size: <meta name="card-size" content="1200x1000">
  const html = fs.readFileSync(path.join(site, job.html), "utf8");
  const m = html.match(/name="card-size" content="(\d+)x(\d+)"/);
  await page.setViewportSize({ width: m ? +m[1] : 1200, height: m ? +m[2] : 630 });
  await page.goto("file://" + path.join(site, job.html), { waitUntil: "load" });
  try { await page.evaluate(() => document.fonts && document.fonts.ready); } catch (e) {}
  await page.screenshot({ path: out, type: "png" });
  done++;
}
await browser.close();
fs.rmSync(dir, { recursive: true, force: true });
console.log(`render_graphics: ${done} cards -> ${site}`);
