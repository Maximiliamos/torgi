# BAT-310: unresolved Git branch disposition

The legacy automatic branch-hygiene workflow already removed branches that
were safely considered merged. Branches containing unique commits are **not**
equivalent to garbage. Do not delete them automatically or assume that their
commit counts imply useful functionality.

The `Audit branch disposition without deletion` workflow runs on
`ubuntu-latest`, has read-only GitHub permissions, and publishes two
artifacts: `branch-inventory.json` and `branch-inventory.md`.
It reads all branch refs, compares each one to the same `main` commit, and
fails closed if `main` moves during the report. It never deletes refs.

For each branch, the owner must choose one disposition and record the reason:

- **Keep** — still in use, with an assigned owner and tracked issue.
- **Cherry-pick** — specific reviewed unique commits add needed functionality;
  create an independently tested PR before archiving old work.
- **Archive** — preserve branch tip SHA in a durable reference or tag; check
  credentials and sensitive history before exposing tags.
- **Delete** — only after reviewed evidence confirms no unique relevant work,
  and the owner explicitly approves deletion.

A branch marked `ahead_by=0` is only a deletion *candidate*, not approval.
A branch with `ahead_by>0` must remain untouched until its unique patches
are classified. Three historically queued workflow runs failed both cancel and
force-cancel with GitHub HTTP 409 and require provider/admin intervention;
those queue records must not cause destruction of branch data.

Required future acceptance: every retained branch has an owner/issue;
every removed branch has a recorded old tip SHA, disposition and review;
no expected unique changes are lost.
