import assert from 'node:assert/strict';
import { test } from 'node:test';
import { checkBuildAudit } from './check-build-audit.mjs';

const advisory = id => ({ url: `https://github.com/advisories/${id}` });
const report = vulnerabilities => ({ vulnerabilities, metadata: { vulnerabilities: { high: 1 } } });

test('allows exact known build advisory and ignores unrelated moderate finding', () => {
  assert.deepEqual(checkBuildAudit(report({
    vite: { name: 'vite', severity: 'high', via: ['picomatch', 'minimatch'] },
    picomatch: { name: 'picomatch', severity: 'high', via: [advisory('GHSA-fc5q-39xc-vjpf')] },
    minimatch: { name: 'minimatch', severity: 'moderate', via: [{ ...advisory('GHSA-other'), severity: 'moderate' }] },
  })), ['GHSA-fc5q-39xc-vjpf']);
});
test('rejects new high finding', () => {
  assert.throws(() => checkBuildAudit(report({ x: { name: 'x', severity: 'high', via: [advisory('GHSA-new')] } })));
});
test('rejects unknown leaf through high meta dependency', () => {
  assert.throws(() => checkBuildAudit(report({ x: { name: 'x', severity: 'high', via: ['missing'] } })));
});
test('rejects incomplete or inconsistent audit', () => {
  assert.throws(() => checkBuildAudit({ error: {} }));
  assert.throws(() => checkBuildAudit(report({})));
});
