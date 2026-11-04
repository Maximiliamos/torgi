"""No ETP false 'full' success from absent next-page links."""
from bankrotai.services.etp_terminal_evidence import reached_verified_terminal


def test_direct_etp_requires_independent_end_proof():
    assert reached_verified_terminal("nistp.ru", {}) is False
    assert reached_verified_terminal("nistp.ru", {"page": 1, "items_on_page": 14}) is False
    assert reached_verified_terminal(
        "nistp.ru", {"source_end_verified": True}
    ) is True
    assert reached_verified_terminal(
        "sberbank-ast.ru", {"total_pages": 3, "progress_current": 3}
    ) is True
    assert reached_verified_terminal(
        "sberbank-ast.ru", {"total_pages": 3, "progress_current": 2}
    ) is False
    assert reached_verified_terminal(
        "sberbank-ast.ru", {"total_pages": "3", "progress_current": 3}
    ) is False


def test_stock_region_scanning_contract_untouched():
    assert reached_verified_terminal("torgi-russia.ru", {}) is True
    assert reached_verified_terminal("lot-online.ru", {}) is True
