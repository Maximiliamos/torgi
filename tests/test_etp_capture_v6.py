"""Safe URL selection for browser ETP collector; no live browsers or network."""
from __future__ import annotations

from scripts.etp_capture_v6 import detail_urls, public_url, safe_url_label


class _Links:
    def __init__(self, values):
        self.values = values

    def evaluate_all(self, code):
        assert "href" in code
        return self.values


class _Page:
    def __init__(self, values):
        self.links = _Links(values)

    def locator(self, css):
        assert css == "a[href]"
        return self.links


def test_browser_collector_rejects_external_hosts_and_url_credentials():
    assert public_url("https://catalog.lot-online.ru/index.php", "catalog.lot-online.ru")
    assert not public_url("https://catalog.lot-online.ru.evil.example/index.php", "catalog.lot-online.ru")
    assert not public_url("http://catalog.lot-online.ru/index.php", "catalog.lot-online.ru")
    assert not public_url("https://user:pass@catalog.lot-online.ru/index.php", "catalog.lot-online.ru")


def test_log_urls_never_include_query_values_or_secret_parameter_names():
    value = safe_url_label(
        "https://m-ets.ru/search?q=some-personal-data&token=private-secret&email=x@y"
    )
    assert value == "https://m-ets.ru/search?query_keys=q"
    assert "private-secret" not in value and "some-personal-data" not in value


def test_lot_online_detail_selector_rejects_catalogue_and_login():
    page = _Page([
        "https://catalog.lot-online.ru/index.php?dispatch=products.view&product_id=1780018",
        "https://catalog.lot-online.ru/index.php?dispatch=categories.view&category_id=9876",
        "https://catalog.lot-online.ru/login",
        "https://other.example/index.php?dispatch=products.view&product_id=1",
    ])
    assert detail_urls(page, "lot-online.ru", "catalog.lot-online.ru") == [
        "https://catalog.lot-online.ru/index.php?dispatch=products.view&product_id=1780018"
    ]


def test_sberbank_lot_view_only_not_a_login_page():
    page = _Page([
        "https://www.sberbank-ast.ru/user/login",
        "https://www.sberbank-ast.ru/ESGoodsList.html",
        "https://www.sberbank-ast.ru/Catalog/NBT/GoodsView/13/0/0/100012",
    ])
    assert detail_urls(page, "sberbank-ast.ru", "www.sberbank-ast.ru") == [
        "https://www.sberbank-ast.ru/Catalog/NBT/GoodsView/13/0/0/100012"
    ]


def test_no_unverified_detail_pattern_means_no_guessed_lots():
    assert detail_urls(_Page(["https://ausib.ru/news/123"]), "ausib.ru", "ausib.ru") == []
