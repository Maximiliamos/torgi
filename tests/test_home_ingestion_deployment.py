from pathlib import Path


WORKFLOW = Path(".github/workflows/home-secondary-deploy.yml")


def test_home_image_build_retries_transient_registry_tls_failures() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")
    assert "for ($attempt = 1; $attempt -le 4; $attempt++)" in workflow
    assert "$usePull = $attempt -eq 1" in workflow
    assert "$timeoutSeconds = if ($usePull) { 300 } else { 1200 }" in workflow
    assert "$mode = if ($usePull) { 'refresh-base' } else { 'cached-base' }" in workflow
    assert "Start-Process -FilePath 'docker.exe'" in workflow
    assert "$arguments = @('build')" in workflow
    assert "if ($usePull) { $arguments += '--pull' }" in workflow
    assert "$process.WaitForExit($timeoutSeconds * 1000)" in workflow
    assert "taskkill.exe /PID $process.Id /T /F" in workflow
    assert "Could not build the current-main API image after four bounded attempts" in workflow


def test_home_dockerfile_caches_dependency_layer_before_app_source() -> None:
    dockerfile = Path("Dockerfile").read_text(encoding="utf-8")

    requirements_copy = "COPY pyproject.toml README.md requirements.lock ./"
    dependency_install = "pip install -r requirements.lock"
    source_copy = "COPY src ./src"
    package_install = "pip install --no-deps ."

    assert "RUN --mount=type=cache,target=/root/.cache/pip" in dockerfile
    assert dockerfile.index(requirements_copy) < dockerfile.index(dependency_install)
    assert dockerfile.index(dependency_install) < dockerfile.index(source_copy)
    assert dockerfile.index(source_copy) < dockerfile.index(package_install)


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



def test_home_deploy_skips_heavy_backup_only_when_schema_is_proven_current() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")

    precheck = 'python -m alembic current --check-heads'
    backup = 'pg_dump `'
    migrate = 'python -m alembic upgrade head'

    assert '$schemaAtHead = $false' in workflow
    assert 'if (-not $schemaAtHead)' in workflow
    assert 'Database schema is already at application head; skipping pre-migration backup and migration' in workflow
    assert 'Database schema is not proven current; preserving pre-migration backup and migration path' in workflow
    assert workflow.index(precheck) < workflow.index(backup) < workflow.index(migrate)


def test_home_deploy_clears_only_orphaned_geo_lock_after_worker_stop() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")
    remove = "docker rm --force $spec.Name"
    clear = "redis-cli DEL bankrotai:geocoding:batch"
    start = '"${env:IMAGE_NAME}:${env:GITHUB_SHA}" @workerArgs'

    assert clear in workflow
    assert workflow.index(remove) < workflow.index(clear) < workflow.index(start)
    assert 'if ($spec.Name -eq $env:GEO_WORKER_CONTAINER)' in workflow


def test_home_deploy_clears_only_orphaned_map_lock_after_worker_stop() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")
    remove = "docker rm --force $spec.Name"
    clear = "redis-cli DEL bankrotai:map-dataset-build"
    start = '"${env:IMAGE_NAME}:${env:GITHUB_SHA}" @workerArgs'

    assert clear in workflow
    assert workflow.index(remove) < workflow.index(clear) < workflow.index(start)
    assert 'if ($spec.Name -eq $env:MAP_WORKER_CONTAINER)' in workflow


def test_home_deploy_recovers_runner_before_checkout_and_never_prunes_volumes() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")

    recovery = "Emergency runner recovery before checkout"
    checkout = "actions/checkout@v7"
    assert recovery in workflow
    assert workflow.index(recovery) < workflow.index(checkout)
    assert "AUTO_MERGE.lock" in workflow
    assert "packed-refs.lock" in workflow
    assert "runnerTemp" in workflow
    assert "docker system prune" not in workflow
    assert "docker volume prune" not in workflow
    assert "Invoke-DockerCleanup" in workflow
    assert "less than 4 GB free" in workflow
    assert "@('restart',$env:CONTAINER_NAME)" in workflow
    assert "Invoke-DockerEmergency" in workflow


def test_home_emergency_docker_probe_is_bounded_and_recovers_service() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")

    assert "Invoke-DockerEmergency" in workflow
    assert "WaitForExit($TimeoutSeconds * 1000)" in workflow
    assert "Restart-Service -Name 'com.docker.service' -Force" in workflow
    assert "Docker daemon remains unavailable after service restart" in workflow
    assert "wsl.exe --shutdown" in workflow
    assert "Docker Desktop/WSL backend recovered successfully" in workflow


def test_legacy_tbankrot_cookie_migration_cannot_block_home_deploy() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")

    assert "Legacy TBankrot session could not be migrated; Auth Center will request a fresh login" in workflow
    assert "Copy-Item -LiteralPath $legacyTbankrotCookiePath" in workflow
    assert "-ErrorAction Stop" in workflow
    assert "try {" in workflow
    assert "catch {" in workflow


def test_home_deploy_pre_migration_backup_uses_d_drive() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")
    assert "$backupRoot = 'D:\\BankrotAI\\db-backups'" in workflow
    assert "Pre-migration backup refused: D: drive is not available" in workflow
    assert "Select-Object -Skip 1" in workflow
