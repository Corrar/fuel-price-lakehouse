// Static checks of the dashboard front-end, run by CI (no browser needed).
//
// - Every JavaScript file parses.
// - Every interface string used in the code exists in js/strings.js, in both
//   languages, and every string defined there is used: a key cannot be added
//   in one language only, or survive the code that needed it.
//
// Usage: node tools/check_site.mjs [site dir]   (default: the package's site/static)
import { execFileSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { pathToFileURL } from "node:url";

const root = path.resolve(process.argv[2] ?? "src/fuel_price_lakehouse/site/static");
const scripts = [];
const walk = (dir) => {
  for (const name of fs.readdirSync(dir)) {
    const file = path.join(dir, name);
    if (fs.statSync(file).isDirectory()) walk(file);
    else if (file.endsWith(".js")) scripts.push(file);
  }
};
walk(path.join(root, "js"));

const problems = [];
for (const file of scripts) {
  try {
    execFileSync(process.execPath, ["--check", file], { stdio: "pipe" });
  } catch (error) {
    problems.push(`does not parse: ${path.relative(root, file)}\n${error.stderr}`);
  }
}

const { STRINGS } = await import(pathToFileURL(path.join(root, "js/strings.js")).href);
const defined = new Set(Object.keys(STRINGS));
const groups = [...new Set([...defined].map((key) => key.split(".")[0]))].join("|");
const used = new Set();
const prefixes = [];
for (const file of scripts) {
  if (file.endsWith("strings.js")) continue;
  const text = fs.readFileSync(file, "utf8");
  for (const match of text.matchAll(new RegExp(`"((?:${groups})\\.[a-zA-Z0-9_.]+)"`, "g"))) {
    used.add(match[1]);
  }
  // Keys built at run time, such as `ab.g.${key}`, count by their fixed prefix.
  for (const match of text.matchAll(new RegExp("`((?:" + groups + ")\\.[^`]*)`", "g"))) {
    prefixes.push(match[1].split("${")[0]);
  }
}
for (const key of used) if (!defined.has(key)) problems.push(`string used but not defined: ${key}`);
for (const key of defined) {
  if (!used.has(key) && !prefixes.some((prefix) => prefix && key.startsWith(prefix))) {
    problems.push(`string defined but never used: ${key}`);
  }
}
for (const [key, value] of Object.entries(STRINGS)) {
  if (!Array.isArray(value) || value.length !== 2 || !value[0] || !value[1]) {
    problems.push(`string not given in both languages: ${key}`);
  }
}

console.log(`${scripts.length} scripts, ${defined.size} interface strings`);
if (problems.length) {
  console.error(problems.join("\n"));
  process.exit(1);
}
console.log("site checks passed");
