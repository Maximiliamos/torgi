from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "home-reverse-relay.yml"
REGRU_WORKFLOW = ROOT / ".github" / "workflows" / "regru-deploy.yml"


def test_home_relay_uses_wss_443_instead_of_raw_ssh() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")
    assert "wss://relay.194-226-126-233.sslip.io" in workflow
    assert "tcp://127.0.0.1:18080:127.0.0.1:18000" in workflow


def test_home_relay_is_authenticated_and_port_restricted() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")
    assert "!Authorization" in workflow
    assert "!ReverseTunnel" in workflow
    assert "port: [18080]" in workflow
    assert "cidr: [127.0.0.1/32]" in workflow
    assert "--http-headers-file" in workflow


def test_home_relay_has_watchdog_and_public_stability_gate() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")
    assert "BankrotAI Home WSS Relay" in workflow
    assert "--websocket-ping-frequency','15s'" in workflow
    assert "Require 20 consecutive public successes" in workflow
    assert "bankrotai-wstunnel.service" in workflow
    assert "ghcr.io/erebe/wstunnel" not in workflow
    assert "docker network inspect bankrotai" in workflow
    assert "ws://${docker_gateway}:18081" in workflow
    assert 'reverse_proxy [^ ]+:18081#reverse_proxy ${docker_gateway}:18081' in workflow
    assert 'reverse_proxy [^ ]+:18080#reverse_proxy ${docker_gateway}:18080' in workflow
    assert 'wget -S -O /dev/null http://${docker_gateway}:18081/' in workflow
    assert "$env:NO_COLOR = 'true'" in workflow


def test_regru_caddy_reaches_loopback_relay_through_host_gateway() -> None:
    workflow = REGRU_WORKFLOW.read_text(encoding="utf-8")
    assert "reverse_proxy ${DOCKER_GATEWAY}:18080" in workflow
    assert "docker network inspect bankrotai" in workflow
    assert "--add-host host.docker.internal:host-gateway" in workflow
