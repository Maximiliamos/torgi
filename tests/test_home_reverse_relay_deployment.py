import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "home-reverse-relay.yml"
REGRU_WORKFLOW = ROOT / ".github" / "workflows" / "regru-deploy.yml"


def test_home_relay_uses_wss_443_instead_of_raw_ssh() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")
    assert "wss://relay.194-226-126-233.sslip.io" in workflow
    assert "tcp://0.0.0.0:18080:127.0.0.1:18000" in workflow


def test_home_relay_is_authenticated_and_port_restricted() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")
    assert "!Authorization" in workflow
    assert "!ReverseTunnel" in workflow
    assert "port: [18080]" in workflow
    assert "cidr: [0.0.0.0/0]" in workflow
    assert "--http-headers-file" in workflow


def test_home_relay_has_watchdog_and_public_stability_gate() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")
    assert "BankrotAI Home WSS Relay" in workflow
    assert "--websocket-ping-frequency','15s'" in workflow
    assert "--connection-min-idle','5'" in workflow
    assert "$commandArgs -ccontains '--connection-min-idle'" in workflow
    assert "function Get-RelayProcesses" in workflow
    assert "$allRelayProcesses.Count -eq 1" in workflow
    assert "$taskStillRunningResult = 267009" in workflow
    assert "Require 20 consecutive public successes" in workflow
    assert "--network bankrotai" in workflow
    assert "RUN chmod 755 /usr/local/bin/bankrotai-wstunnel" in workflow
    assert "ghcr.io/erebe/wstunnel" not in workflow
    assert "ws://0.0.0.0:18081" in workflow
    assert 'reverse_proxy [^ ]+:18081#reverse_proxy bankrotai-wstunnel:18081' in workflow
    assert 'reverse_proxy [^ ]+:18080#reverse_proxy bankrotai-wstunnel:18080' in workflow
    assert 'wget -S -O /dev/null http://bankrotai-wstunnel:18081/' in workflow
    assert "restrictions.yaml.next" in workflow
    assert "$env:NO_COLOR = 'true'" in workflow


def test_home_relay_bypasses_the_workstation_vpn_for_regru_only() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")
    assert "Pin REG.RU relay outside the workstation VPN" in workflow
    assert "$destination = '194.226.126.233/32'" in workflow
    assert "Get-NetAdapter -Physical" in workflow
    assert "DestinationPrefix '0.0.0.0/0'" in workflow
    assert "Get-CimInstance Win32_IP4PersistedRouteTable" in workflow
    assert "route.exe -p add 194.226.126.233 mask 255.255.255.255" in workflow
    assert "PolicyStore ActiveStore" in workflow
    assert "REG.RU relay route did not converge on the physical uplink" in workflow


def test_regru_caddy_routes_application_api_through_home_relay() -> None:
    workflow = REGRU_WORKFLOW.read_text(encoding="utf-8")
    assert len(re.findall(r"reverse_proxy bankrotai-wstunnel:18080", workflow)) == 2
    assert "docker network inspect bankrotai" in workflow
    assert "--add-host host.docker.internal:host-gateway" in workflow
    assert "NEON_DATABASE_URL" not in workflow
    assert "NEON_DATABASE_MIGRATION_URL" not in workflow
    assert "python -m bankrotai.cli init-db" not in workflow
    assert "docker rm -f bankrotai-api" not in workflow
    assert "--name bankrotai-api" not in workflow
    assert "Build and publish API image" not in workflow
    assert "Keep the legacy bankrotai-api container and image untouched" in workflow


def test_regular_regru_deploy_preserves_wss_ingress() -> None:
    workflow = REGRU_WORKFLOW.read_text(encoding="utf-8")
    assert "WSS_HOSTNAME: relay.194-226-126-233.sslip.io" in workflow
    assert "WSS_HOSTNAME='$WSS_HOSTNAME' bash -s" in workflow
    assert len(re.findall(r"^\s*\$\{WSS_HOSTNAME\} \{", workflow, re.MULTILINE)) == 1
    assert re.search(
        r"\$\{WSS_HOSTNAME\} \{\s+reverse_proxy bankrotai-wstunnel:18081\s+"
        r'header Cache-Control "no-store"',
        workflow,
    )


def test_regru_deploy_gates_switch_on_staged_home_api_and_dataset() -> None:
    workflow = REGRU_WORKFLOW.read_text(encoding="utf-8")
    assert "for stability_check in $(seq 1 20)" in workflow
    assert '"https://$HOME_RELAY_HOSTNAME/health/live"' in workflow
    assert '"https://$HOME_RELAY_HOSTNAME/health/ready"' in workflow
    assert '"https://$HOME_RELAY_HOSTNAME/api/map/datasets/current"' in workflow
    assert 'd["version"]' in workflow
    assert 'd["point_count"] > 0' in workflow
    assert 'd["tile_count"] > 0' in workflow
    assert 'd["published_at"]' in workflow
    assert "docker restart bankrotai-cloudflared" not in workflow


def test_api_proxy_promotes_home_relay_and_keeps_legacy_read_fallback() -> None:
    config = (ROOT / "WEB" / "api-proxy" / "wrangler.jsonc").read_text(encoding="utf-8")
    worker = (ROOT / "WEB" / "api-proxy" / "worker.mjs").read_text(encoding="utf-8")
    assert '"PRIMARY_API_ORIGIN": "https://home-relay.194-226-126-233.sslip.io"' in config
    assert '"SECONDARY_API_ORIGIN": "https://194-226-126-233.sslip.io"' in config
    assert 'DEFAULT_PRIMARY_ORIGIN = "https://home-relay.194-226-126-233.sslip.io"' in worker
    assert "const fallbackOrigin = SAFE_METHODS.has(request.method)" in worker
