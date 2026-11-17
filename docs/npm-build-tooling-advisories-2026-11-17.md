# Build tooling advisory disposition — 17 November 2026

CI newly reports GHSA-fc5q-39xc-vjpf in picomatch <=4.0.7. The upstream
[GitHub advisory](https://github.com/advisories/GHSA-fc5q-39xc-vjpf) states
that no patched release is available; npm registry latest is 4.0.7.
`npm audit fix --force` suggests changing the Vitest major and is not a
validated fix for the affected parser.

Vite, TypeScript and the React Vite plugin are now correctly classified as
devDependencies. The WEB Docker build installs them to compile trusted
repository sources; the final nginx image receives only dist, not node_modules
or the Node runtime. Public application input does not reach this glob parser.
The isolated production dependency audit reports zero vulnerabilities.

This is **containment and a scoped build-tooling exception, not a vulnerability
patch**. Existing CI disposition for unpatched development-only braces advisory
GHSA-vfj7-8cjw-p6xm is extended only to GHSA-fc5q-39xc-vjpf. Every other
high/critical advisory and every runtime high/critical advisory still fails CI;
audit infrastructure failure also fails. Full findings remain visible in logs.

Do not expose a Vite development server publicly or pass untrusted glob patterns
to build tooling. Replace this exception with a patched compatible release when
upstream publishes it. Version checks must use upstream advisory and npm registry,
not an invented version or an audit suppression presented as remediation.
