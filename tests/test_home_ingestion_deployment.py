from pathlib import Path


WORKFLOW = Path(".github/workflows/home-secondary-deploy.yml")


def test_home_deploy_keeps_public_api_read_only_and_runs_private_worker() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")

    assert "'API_READ_ONLY=true'" in workflow
    assert "'API_READ_ONLY=false'" in workflow
    assert "WORKER_CONTAINER: bankrotai-home-ingestion-worker" in workflow
    assert "REDIS_CONTAINER: bankrotai-home-redis" in workflow
    assert "celery -A bankrotai.tasks:celery_app worker" in workflow
    assert "--network $env:DOCKER_NETWORK" in workflow


def test_home_redis_is_authenticated_persistent_and_not_published() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")

    assert "requirepass $redisPassword" in workflow
    assert "--volume bankrotai-home-redis:/data" in workflow
    assert "redis-password.txt" in workflow
    assert "REDIS_URL=redis://:${redisPassword}@${env:REDIS_CONTAINER}:6379/0" in workflow
    assert "--publish 127.0.0.1:6379:6379" not in workflow
    assert "--publish 0.0.0.0:6379:6379" not in workflow
