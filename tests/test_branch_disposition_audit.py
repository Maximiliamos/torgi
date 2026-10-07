"""Branch inventory never approves deletion or trusts moving main."""

from __future__ import annotations

import pytest

from scripts import branch_disposition_audit as inventory


def fake_source(repo: str, path: str, token: str):
    assert repo == "Maximiliamos/torgi"
    assert token == "token"
    if path == "branches/main":
        return {"commit": {"sha": "a" * 40}}
    if path == "branches?per_page=100&page=1":
        return [
            {"name": "feature/keep-me", "commit": {"sha": "b" * 40}},
            {"name": "main", "commit": {"sha": "a" * 40}},
            {"name": "fix/merged", "commit": {"sha": "c" * 40}},
        ]
    if "feature%2Fkeep-me" in path:
        return {"ahead_by": 5, "behind_by": 7, "status": "diverged"}
    if "fix%2Fmerged" in path:
        return {"ahead_by": 0, "behind_by": 4, "status": "behind"}
    raise AssertionError(path)


def test_inventory_is_read_only_and_preserves_unique_branch(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(inventory, "fetch_json", fake_source)
    data = inventory.audit("Maximiliamos/torgi", "token")
    assert data["branch_count"] == 3
    assert data["unique_unmerged"] == 1
    assert data["no_unique_commits"] == 1
    assert all(not branch["approved_for_deletion"] for branch in data["branches"])
    rendered = inventory.as_markdown(data)
    assert "feature/keep-me" in rendered
    assert "preserve for review" in rendered
    assert "No branch is automatically approved for deletion" in rendered


def test_main_movement_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    reads = [0]

    def moving(repo: str, path: str, token: str):
        if path == "branches/main":
            reads[0] += 1
            if reads[0] > 1:
                return {"commit": {"sha": "changed"}}
        return fake_source(repo, path, token)

    monkeypatch.setattr(inventory, "fetch_json", moving)
    with pytest.raises(RuntimeError, match="main moved"):
        inventory.audit("Maximiliamos/torgi", "token")
