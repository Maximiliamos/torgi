/* global process */

import { execFileSync } from "node:child_process";
import { mkdirSync, writeFileSync } from "node:fs";

function currentCommit() {
  const fromEnvironment = process.env.CF_PAGES_COMMIT_SHA || process.env.GITHUB_SHA;
  if (fromEnvironment) return fromEnvironment.trim();
  return execFileSync("git", ["rev-parse", "HEAD"], { encoding: "utf8" }).trim();
}

const commit = currentCommit();
if (!/^[0-9a-f]{40}$/i.test(commit)) {
  throw new Error("Deployment commit must be a full 40-character Git SHA");
}

mkdirSync("dist", { recursive: true });
writeFileSync("dist/deployment.json", `${JSON.stringify({ commit })}\n`, "utf8");
