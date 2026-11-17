import fs from 'node:fs';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

const allowed = new Set(['GHSA-vfj7-8cjw-p6xm', 'GHSA-fc5q-39xc-vjpf']);

// Runtime npm audit must pass independently before this build-only disposition.
export function checkBuildAudit(report) {
  if (report.error || !report.vulnerabilities || !report.metadata?.vulnerabilities) {
    throw new Error('Incomplete npm audit report');
  }
  const exceptions = new Set();
  function inspect(name, seen = new Set()) {
    if (seen.has(name)) throw new Error(`Cyclic advisory chain: ${name}`);
    const item = report.vulnerabilities[name];
    if (!item?.via?.length) throw new Error(`Unproven advisory chain: ${name}`);
    const next = new Set([...seen, name]);
    for (const via of item.via) {
      if (typeof via === 'string') inspect(via, next);
      else {
        if (['info', 'low', 'moderate'].includes(via.severity)) continue;
        const advisory = /^https:\/\/github\.com\/advisories\/(GHSA-[a-z0-9-]+)$/.exec(via.url ?? '')?.[1];
        if (!allowed.has(advisory)) throw new Error(`Unexpected high/critical dependency chain: ${name}: ${via.url}`);
        exceptions.add(advisory);
      }
    }
  }
  const high = Object.values(report.vulnerabilities).filter(v => ['high', 'critical'].includes(v.severity));
  for (const item of high) inspect(item.name);
  if (high.length && !exceptions.size) throw new Error('High severity chains lack allowlisted high advisory evidence');
  if (!high.length && (report.metadata.vulnerabilities.high || report.metadata.vulnerabilities.critical)) {
    throw new Error('High severity metadata lacks advisory evidence');
  }
  return [...exceptions].sort();
}

if (process.argv[1] && fileURLToPath(import.meta.url) === path.resolve(process.argv[1])) {
  try {
    const exceptions = checkBuildAudit(JSON.parse(fs.readFileSync(0, 'utf8')));
    if (exceptions.length) console.log(`::warning::Known unpatched build-only advisories: ${exceptions.join(', ')}; runtime audited separately.`);
  } catch (error) {
    console.error(error.message);
    process.exitCode = 1;
  }
}
