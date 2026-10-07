# Repository hygiene

`.github/workflows/branch-hygiene.yml` removes only disposable branches that are not protected and are not heads of open pull requests.

A branch is eligible when either:

1. GitHub records a merged pull request whose head is that branch in this repository; or
2. the branch has no commits ahead of `main`.

Branches with unique commits and no merged PR remain untouched for manual classification. This intentionally prefers false negatives over deleting unmerged work.
