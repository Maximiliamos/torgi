from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BROKER = ROOT / "tbankrot-auth" / "server.mjs"
BROKER_DOCKERFILE = ROOT / "Dockerfile.tbankrot-auth"
HOME_DEPLOY = ROOT / ".github" / "workflows" / "home-secondary-deploy.yml"
CI = ROOT / ".github" / "workflows" / "ci.yml"
API = ROOT / "src" / "bankrotai" / "api.py"
WEB = ROOT / "WEB" / "src" / "main.tsx"
WEB_API = ROOT / "WEB" / "src" / "lib" / "api.ts"
PHASE3_HEALTH = ROOT / "scripts" / "phase3-production-health.ps1"
P2_MAINTENANCE = ROOT / "scripts" / "p2-production-maintenance.ps1"
API_PROXY = ROOT / "WEB" / "api-proxy" / "worker.mjs"


def test_tbankrot_browser_broker_is_origin_bounded_and_never_exposes_cookies() -> None:
    broker = BROKER.read_text(encoding="utf-8")

    assert 'const TARGET_ORIGIN = "https://tbankrot.ru"' in broker
    assert 'endsWith("tbankrot.ru")' in broker
    assert "timingSafeEqual" in broker
    assert "hasSearchEvidence" in broker
    assert "auth_required" in broker
    assert 'type === "click"' in broker
    assert 'type === "text"' in broker
    assert 'type === "key"' in broker
    assert 'type === "wheel"' in broker
    assert 'type === "reload"' in broker
    assert 'type === "back"' in broker
    assert 'type === "navigate"' not in broker
    assert "fsp.rename(temporary, COOKIE_FILE)" in broker
    assert "response.end(image)" in broker
    assert "cookies:" not in broker.split('if (url.pathname === "/status"', 1)[1].split("if (url.pathname ===", 1)[0]


def test_tbankrot_broker_is_isolated_from_main_runtime_secrets() -> None:
    deploy = HOME_DEPLOY.read_text(encoding="utf-8")

    assert "Dockerfile.tbankrot-auth" in deploy
    assert "bankrotai-tbankrot-auth" in deploy
    assert "TBANKROT_AUTH_BROKER_TOKEN" in deploy
    assert "TBANKROT_AUTH_BROKER_URL=http://bankrotai-tbankrot-auth:18443" in deploy
    assert "tbankrot-auth,target=/run/secrets/tbankrot,readonly" in deploy
    assert 'target=/data"' in deploy
    assert "--read-only" in deploy
    assert "--shm-size 512m" in deploy
    assert "No TBankrot session yet; web Auth Center will create it on first login" in deploy
    assert "Protected TBankrot session is missing" not in deploy

    broker_start = deploy.split("--name $env:TBANKROT_AUTH_CONTAINER", 1)[1].split(
        "$existing = docker ps", 1
    )[0]
    assert "--network $env:DOCKER_NETWORK" in broker_start
    assert "--publish" not in broker_start
    assert "DATABASE_URL" not in broker_start
    assert "REDIS_URL" not in broker_start
    assert "MAP_OBJECT_STORE_SECRET_KEY" not in broker_start


def test_p5_api_only_runs_targeted_tbankrot_after_auth_verification() -> None:
    api = API.read_text(encoding="utf-8")

    assert '@app.get("/api/tbankrot/status")' in api
    assert '@app.post("/api/tbankrot/auth/start")' in api
    assert '@app.get("/api/tbankrot/auth/{session_id}/frame")' in api
    assert '@app.post("/api/tbankrot/auth/{session_id}/action")' in api
    assert '@app.post("/api/tbankrot/auth/{session_id}/verify")' in api
    assert '@app.post("/api/tbankrot/auth/{session_id}/close")' in api
    assert '@app.post("/api/tbankrot/sync")' in api
    assert 'mode="source:tbankrot.ru"' in api
    assert "probe_tbankrot_saved_session" in api
    assert 'status_code=428' in api
    assert 'pattern="^(click|text|key|wheel|reload|back)$"' in api
    assert '"navigate"' not in api.split("class TBankrotBrowserActionRequest", 1)[1].split(
        "class OnlineLotImportRequest", 1
    )[0]


def test_web_has_first_class_tbankrot_auth_center_and_attention_indicator() -> None:
    web = WEB.read_text(encoding="utf-8")
    web_api = WEB_API.read_text(encoding="utf-8")

    assert '["tbankrot", "TBankrot", <ShieldCheck />]' in web
    assert "<TBankrotView" in web
    assert "tbankrotNeedsAttention" in web
    assert "Перейти к авторизации TBankrot" in web
    assert "err.status === 428" in web
    assert "fetchTBankrotStatus" in web_api
    assert "fetchTBankrotFrame" in web_api
    assert "verifyTBankrotAuth" in web_api
    assert "syncTBankrot" in web_api


def test_p5_is_covered_by_existing_production_health_and_maintenance() -> None:
    phase3 = PHASE3_HEALTH.read_text(encoding="utf-8")
    p2 = P2_MAINTENANCE.read_text(encoding="utf-8")
    ci = CI.read_text(encoding="utf-8")

    assert "'bankrotai-tbankrot-auth'" in phase3
    assert "tbankrot-auth-broker" in phase3
    assert "'bankrotai-tbankrot-auth'" in p2
    assert "node --check ../tbankrot-auth/server.mjs" in ci
    assert "playwright:v1.55.1-noble" in BROKER_DOCKERFILE.read_text(encoding="utf-8")



def test_tbankrot_auth_center_has_home_only_proxy_budget() -> None:
    proxy = API_PROXY.read_text(encoding="utf-8")

    assert "TBANKROT_SAFE_TIMEOUT_MS = 20_000" in proxy
    assert "TBANKROT_MUTATION_TIMEOUT_MS = 55_000" in proxy
    assert 'incoming.pathname.startsWith("/api/tbankrot/")' in proxy
    assert "SAFE_METHODS.has(request.method) && !tbankrotRequest" in proxy
