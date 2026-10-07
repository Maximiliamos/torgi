#!/usr/bin/env python3
"""Inventory GitHub branches safely, without mutation or deletion."""

from __future__ import annotations

import argparse
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


def fetch_json(repo: str, endpoint: str, token: str) -> dict | list:
    req = urllib.request.Request(
        f"https://api.github.com/repos/{repo}/{endpoint}",
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "User-Agent": "sterdez-branch-disposition-audit",
        },
    )
    with urllib.request.urlopen(req, timeout=35) as response:
        return json.load(response)


def classify(ahead: int, behind: int) -> str:
    if ahead == 0:
        return "merged / potentially removable after verification"
    if behind == 0:
        return "unique commits / current lineage — review"
    return "diverged with unique commits — preserve for review"


def audit(repo: str, token: str) -> dict:
    """Recheck main at end so a moving baseline can never look final."""
    base = fetch_json(repo, "branches/main", token)["commit"]["sha"]
    page = 1
    branches: list[dict] = []
    while True:
        rows = fetch_json(repo, f"branches?per_page=100&page={page}", token)
        if not isinstance(rows, list):
            raise RuntimeError("Unexpected GitHub branch listing payload")
        branches.extend(rows)
        if len(rows) < 100:
            break
        page += 1
    output = []
    for branch in sorted(branches, key=lambda b: b["name"]):
        name = branch["name"]
        if name == "main":
            continue
        escaped = urllib.parse.quote(name, safe="")
        relation = fetch_json(repo, f"compare/{base}...{escaped}", token)
        ahead = relation["ahead_by"]
        behind = relation["behind_by"]
        output.append(
            {
                "name": name,
                "sha": branch["commit"]["sha"],
                "ahead_by": ahead,
                "behind_by": behind,
                "status": relation["status"],
                "disposition": classify(ahead, behind),
                "approved_for_deletion": False,
            }
        )
    if fetch_json(repo, "branches/main", token)["commit"]["sha"] != base:
        raise RuntimeError("main moved during inventory; reject stale report")
    return {
        "repository": repo,
        "main_sha": base,
        "branch_count": len(branches),
        "unique_unmerged": sum(item["ahead_by"] > 0 for item in output),
        "no_unique_commits": sum(item["ahead_by"] == 0 for item in output),
        "branches": output,
    }


def as_markdown(data: dict) -> str:
    lines = [
        "# Branch disposition inventory (read-only)",
        "",
        f"Repository: \`{data['repository']}\`",
        f"Baseline main SHA: \`{data['main_sha']}\`",
        f"Branches including main: **{data['branch_count']}**",
        f"Branches with unique commits: **{data['unique_unmerged']}**",
        f"Branches with no unique commits: **{data['no_unique_commits']}**",
        "",
        "**No branch is automatically approved for deletion.** Ahead counts measure",
        "unique Git commits, not necessarily unique behavior; inspect diffs and",
        "existing PRs before cherry-picking, archiving or deleting.",
        "",
        "| Branch | HEAD | Ahead | Behind | Suggested disposition |",
        "|---|---|---:|---:|---|",
    ]
    for row in data["branches"]:
        safe_name = row["name"].replace("|", "\\|").replace("\n", "")
        lines.append(
            f"| \`{safe_name}\` | \`{row['sha'][:12]}\` | {row['ahead_by']} "
            f"| {row['behind_by']} | {row['disposition']} |"
        )
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY"))
    parser.add_argument("--json", type=Path, required=True)
    parser.add_argument("--markdown", type=Path, required=True)
    args = parser.parse_args()
    if not args.repo or len(args.repo.split("/")) != 2:
        parser.error("Expected owner/repository")
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        parser.error("GITHUB_TOKEN read-only credential is required")
    data = audit(args.repo, token)
    args.json.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    args.markdown.write_text(as_markdown(data), encoding="utf-8")
    print(f"{data['branch_count']} branches, {data['unique_unmerged']} with unique commits")


if __name__ == "__main__":
    main()
