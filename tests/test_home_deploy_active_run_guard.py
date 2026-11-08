"""Prevent deployment of new code over a running durable nationwide ingestion."""
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_home_deploy_refuses_active_nationwide_worker_replacement() -> None:
    workflow = (ROOT / ".github/workflows/home-secondary-deploy.yml").read_text(
        encoding="utf-8"
    )
    gate = workflow.index("- name: Refuse production redeploy while nationwide ingestion is active")
    migrate = workflow.index("- name: Backup and migrate database schema before replacing production containers")
    replace = workflow.index("- name: Start private queue and replace API and trusted ingestion worker")
    assert gate < migrate < replace
    block = workflow[gate:migrate]
    assert 'select(LotSyncRun.id).where(LotSyncRun.status.in_(("queued", "running")))' in block
    assert "NATIONWIDE_INGESTION_IDLE" in block
    assert "docker exec -i bankrotai-home-ingestion-worker python -" in block
    assert "Production redeploy blocked until durable nationwide ingestion is terminal" in block
    assert "worker unavailable" in block
