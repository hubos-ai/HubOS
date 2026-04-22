#!/usr/bin/env node
/**
 * web_crawl/scripts/search.mjs
 * xcrawl 网络搜索 — 返回标题、URL、摘要和 AI 生成答案
 *
 * 用法:
 *   node search.mjs "搜索词"
 *   node search.mjs "搜索词" -n 10
 *   node search.mjs "搜索词" --deep
 */

function usage() {
  console.error(
    "Usage: search.mjs \"query\" [-n <count>] [--deep] [--json]"
  );
  process.exit(2);
}

const args = process.argv.slice(2);
if (args.length === 0 || args[0] === "-h" || args[0] === "--help") usage();

const query = args[0];
let n = 5;
let deep = false;
let jsonOut = false;

for (let i = 1; i < args.length; i++) {
  const a = args[i];
  if (a === "-n") { n = parseInt(args[++i] ?? "5", 10); continue; }
  if (a === "--deep") { deep = true; continue; }
  if (a === "--json") { jsonOut = true; continue; }
  console.error(`Unknown arg: ${a}`); usage();
}

// Load API key from ~/.xcrawl/config.json or env
import { readFileSync, existsSync } from "fs";
import { homedir } from "os";
import { join } from "path";

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

const body = {
  query,
  max_results: Math.max(1, Math.min(n, 20)),
  include_answer: true,
  include_raw_content: false,
};
if (deep) body.search_depth = "advanced";

const resp = await fetch("https://run.xcrawl.com/v1/search", {
  method: "POST",
  headers: { "Content-Type": "application/json", Authorization: `Bearer ${apiKey}` },
  body: JSON.stringify(body),
});

if (!resp.ok) {
  const text = await resp.text().catch(() => "");
  throw new Error(`xcrawl search failed (${resp.status}): ${text}`);
}

const data = await resp.json();
const results = (data.data?.data ?? data.results ?? []);
const answer = data.data?.answer ?? data.answer ?? "";

if (jsonOut) {
  console.log(JSON.stringify({ answer, results, credits: data.data?.credits_used }, null, 2));
  process.exit(0);
}

if (answer) {
  console.log("## 综合答案\n");
  console.log(answer);
  console.log();
}

console.log(`## 搜索结果（共 ${results.length} 条）\n`);
for (const r of results) {
  console.log(`### ${r.title ?? "(无标题)"}`);
  console.log(`URL: ${r.url}`);
  if (r.description) console.log(`摘要: ${r.description}`);
  console.log();
}
