from pathlib import Path


WORKFLOW = Path(".github/workflows/home-secondary-deploy.yml")


def test_home_image_build_retries_transient_registry_tls_failures() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")
    assert "for ($attempt = 1; $attempt -le 4; $attempt++)" in workflow
    assert "docker build --pull" in workflow
    assert "Could not build the current-main API image after four attempts" in workflow


def test_home_deploy_keeps_public_api_read_only_and_runs_private_worker() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")

    assert "'API_READ_ONLY=true'" in workflow
    assert "'API_READ_ONLY=false'" in workflow
    assert "WORKER_CONTAINER: bankrotai-home-ingestion-worker" in workflow
    assert "GEO_WORKER_CONTAINER: bankrotai-home-geocoding-worker" in workflow
    assert "MAP_WORKER_CONTAINER: bankrotai-home-map-worker" in workflow
    assert "REDIS_CONTAINER: bankrotai-home-redis" in workflow
    assert "'celery', '-A', 'bankrotai.tasks:celery_app', 'worker'" in workflow
    assert "Queue = 'ingestion,maintenance'" in workflow
    assert "Queue = 'geocoding'" in workflow
    assert "Queue = 'map'" in workflow
    assert "--concurrency=1" in workflow
    assert "--no-healthcheck" in workflow
    assert "--network $env:DOCKER_NETWORK" in workflow


def test_home_redis_is_authenticated_persistent_and_not_published() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")

    assert "requirepass $redisPassword" in workflow
    assert "--volume bankrotai-home-redis:/data" in workflow
    assert "redis-password.txt" in workflow
    assert "REDIS_URL=redis://:${redisPassword}@${env:REDIS_CONTAINER}:6379/0" in workflow
    assert 'docker exec --env "REDISCLI_AUTH=$redisPassword"' in workflow
    assert "redis-cli -a" not in workflow
    assert "--publish 127.0.0.1:6379:6379" not in workflow
    assert "--publish 0.0.0.0:6379:6379" not in workflow


def test_home_deploy_preserves_local_database_and_only_checks_schema() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")

    assert "Restore latest encrypted Neon backup" not in workflow
    assert "BACKUP_ENCRYPTION_PASSWORD" not in workflow
    assert "gh run download" not in workflow
    assert "pg_restore" not in workflow
    assert "python -m bankrotai.cli init-db" not in workflow
    assert "python -m alembic current --check-heads" in workflow
