from pathlib import Path


RELAY_WORKFLOW = Path(".github/workflows/home-reverse-relay.yml")
REGRU_WORKFLOW = Path(".github/workflows/regru-deploy.yml")


def test_reverse_relay_is_loopback_only_and_uses_the_home_api() -> None:
    workflow = RELAY_WORKFLOW.read_text(encoding="utf-8")

    assert "127.0.0.1:18080:127.0.0.1:18000" in workflow
    assert 'permitlisten=\"127.0.0.1:' in workflow
    assert "GatewayPorts yes" not in workflow
    assert "0.0.0.0:18080" not in workflow


def test_reverse_relay_uses_a_restricted_identity_and_watchdog() -> None:
    workflow = RELAY_WORKFLOW.read_text(encoding="utf-8")

    assert "bankrotai_home_relay_ed25519" in workflow
    assert "restrict,port-forwarding,permitlisten=" in workflow
    assert "REGRU_SSH_PRIVATE_KEY" in workflow
    assert "BankrotAI Home Reverse Relay" in workflow
    assert "ServerAliveInterval=15" in workflow
    assert "ExitOnForwardFailure=yes" in workflow


def test_regru_caddy_exposes_the_relay_through_host_gateway() -> None:
    workflow = REGRU_WORKFLOW.read_text(encoding="utf-8")

    assert "HOME_RELAY_HOSTNAME: home-relay.194-226-126-233.sslip.io" in workflow
    assert "reverse_proxy host.docker.internal:18080" in workflow
    assert "--add-host host.docker.internal:host-gateway" in workflow
