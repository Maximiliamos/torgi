# BAT-308 monolith shrink budget

This repository protects new modules with a hard 1,000-line limit.
Legacy modules in `scripts/check_module_sizes.py` have a *second*
constraint: each has a maximum line count equal to its audited size at
`3bd9e4baa6b2ce339fb51744830211c233628a31`.

As smaller services, route handlers, widgets, and map hooks are extracted,
reduce the named grandfathered budget in the same PR. When a module drops
below 1,000 lines, delete its allowlist entry entirely.

Do not increase budgets to work around CI. Exception requests must record
the owner, justification, rollback, and follow-up reduction milestone in
BAT-308 #886. This guard does not itself achieve modularization; it prevents
the remaining monoliths from silently growing while safe extractions proceed.
