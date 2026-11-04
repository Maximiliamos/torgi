"""Transport diagnostics never follow redirects or emit raw provider pages."""
from __future__ import annotations

from bankrotai.services.etp_transport_diagnostics import classify_redirect, safe_url_label


def test_safe_url_label_never_logs_queries_credentials_or_fragments():
    url = "https://user:password@ausib.ru/etp/trade/list.html?token=secret#cookie"
    label = safe_url_label(url)
    assert "password" not in label and "token" not in label
    assert "secret" not in label and "cookie" not in label
    assert label == "https://ausib.ru/etp/trade/list.html"


def test_redirect_diagnostics_do_not_authorize_cross_host_or_downgrade():
    origin = "https://ausib.ru/etp/trade/list.html"
    assert classify_redirect("ausib.ru", "/etp/trade/list.html?page=2", origin) == (
        "allowed_host_observed_not_followed"
    )
    assert classify_redirect("ausib.ru", "https://www.ausib.ru/", origin) == (
        "outside_configured_hosts"
    )
    assert classify_redirect("ausib.ru", "https://evil.example/etp/", origin) == (
        "outside_configured_hosts"
    )
    assert classify_redirect("ausib.ru", "http://ausib.ru/", origin) == (
        "untrusted_scheme"
    )
    assert classify_redirect("ausib.ru", "https://user:pass@ausib.ru/", origin) == (
        "untrusted_authority"
    )


def test_non_http_redirect_is_redacted():
    assert safe_url_label("javascript:alert(1)") == "<non-http-redirect>"
