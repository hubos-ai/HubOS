#!/usr/bin/env node
/**
 * web_crawl/scripts/scrape.mjs
 * xcrawl 网页抓取 — 将指定 URL 正文转为 Markdown
 * 支持 SPA / JS 渲染页面（xcrawl 使用无头浏览器）
 *
 * 用法:
 *   node scrape.mjs "https://example.com"
 *   node scrape.mjs "https://example.com" --spa
 *   node scrape.mjs "https://example.com" --depth 2
 *   node scrape.mjs "https://url1.com" "https://url2.com"   # 批量（逐个）
 */

import { readFileSync, existsSync } from "fs";
import { homedir } from "os";
import { join } from "path";

function usage() {
  console.error(
    'Usage: scrape.mjs "url1" ["url2" ...] [--spa] [--depth <n>] [--json]'
  );
  process.exit(2);
}

const args = process.argv.slice(2);
if (args.length === 0 || args[0] === "-h" || args[0] === "--help") usage();

const urls = [];
let spa = false;
let depth = 0; // 0 = single page only (no crawl)
let jsonOut = false;

for (let i = 0; i < args.length; i++) {
  const a = args[i];
  if (a === "--spa") { spa = true; continue; }
  if (a === "--depth") { depth = parseInt(args[++i] ?? "1", 10); continue; }
  if (a === "--json") { jsonOut = true; continue; }
  if (a.startsWith("-")) { console.error(`Unknown arg: ${a}`); usage(); }
  urls.push(a);
}
if (urls.length === 0) usage();

// Load API key
let apiKey = (process.env.XCRAWL_API_KEY ?? "").trim();
if (!apiKey) {
  const cfgPath = join(homedir(), ".xcrawl", "config.json");
  if (existsSync(cfgPath)) {
    try {
      const cfg = JSON.parse(readFileSync(cfgPath, "utf-8"));
      apiKey = (cfg.XCRAWL_API_KEY ?? "").trim();
    } catch {}
  }
}
if (!apiKey) {
  console.error("Missing XCRAWL_API_KEY. Set env var or add to ~/.xcrawl/config.json");
  process.exit(1);
}

async function pollResult(scrapeId, maxWait = 30) {
  for (let i = 0; i < maxWait; i++) {
    await new Promise(r => setTimeout(r, i === 0 ? 800 : 2000));
    const r = await fetch(`https://run.xcrawl.com/v1/scrape/${scrapeId}`, {
      headers: { Authorization: `Bearer ${apiKey}` },
    });
    if (!r.ok) continue;
    const d = await r.json();
    if (d.status === "completed") return d.data?.markdown ?? "";
    if (d.status === "failed") return null;
  }
  return null;
}

async function scrapeUrl(url) {
  const payload = {
    url,
    mode: spa ? "async" : "sync",
    output: { formats: ["markdown"] },
  };
  if (depth > 0) payload.depth = depth;

  const resp = await fetch("https://run.xcrawl.com/v1/scrape", {
    method: "POST",
    headers: { "Content-Type": "application/json", Authorization: `Bearer ${apiKey}` },
    body: JSON.stringify(payload),
  });

  if (!resp.ok) {
    const t = await resp.text().catch(() => "");
    return { url, success: false, error: `HTTP ${resp.status}: ${t}` };
  }

  const d = await resp.json();

  // sync completed immediately
  if (d.status === "completed") {
    const md = d.data?.markdown ?? "";
    return { url, success: true, content: md, chars: md.length };
  }

  // async: poll
  const scrapeId = d.scrape_id;
  if (scrapeId) {
    const md = await pollResult(scrapeId);
    if (md !== null) return { url, success: true, content: md, chars: md.length };
    return { url, success: false, error: "Polling timed out" };
  }

  return { url, success: false, error: "Unexpected response format" };
}

const allResults = [];
for (const url of urls) {
  const result = await scrapeUrl(url);
  allResults.push(result);
}

if (jsonOut) {
  console.log(JSON.stringify(allResults, null, 2));
  process.exit(0);
}

for (const r of allResults) {
  if (!r.success) {
    console.log(`# ${r.url}\n\n⚠️ 抓取失败: ${r.error}\n\n---\n`);
    continue;
  }
  const preview = r.content.length > 12000
    ? r.content.slice(0, 12000) + "\n\n… (内容已截断，共 " + r.chars + " 字符)"
    : r.content;
  console.log(`# ${r.url}\n\n${preview}\n\n---\n`);
}
