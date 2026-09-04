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


def test_regru_caddy_reaches_loopback_relay_through_host_gateway() -> None:
    workflow = REGRU_WORKFLOW.read_text(encoding="utf-8")
    assert "reverse_proxy bankrotai-wstunnel:18080" in workflow
    assert "docker network inspect bankrotai" in workflow
    assert "--add-host host.docker.internal:host-gateway" in workflow


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


def test_regru_deploy_waits_for_cloudflared_ready_endpoint_after_restart() -> None:
    workflow = REGRU_WORKFLOW.read_text(encoding="utf-8")
    assert "docker restart bankrotai-cloudflared" in workflow
    assert "--network container:bankrotai-cloudflared" in workflow
    assert "--entrypoint wget caddy:2-alpine -qO- -T 2 http://127.0.0.1:20241/ready" in workflow
    assert "for attempt in $(seq 1 15)" in workflow
    assert 'test "$tunnel_ready" = true' in workflow
    assert "docker exec bankrotai-cloudflared wget -qO- -T 1" not in workflow
    assert "docker logs --since" not in workflow
