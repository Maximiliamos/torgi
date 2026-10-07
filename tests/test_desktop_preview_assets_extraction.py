"""Desktop preview string extraction remains safe without Qt runtime."""

from pathlib import Path

from bankrotai.desktop_preview_assets import (
    MAP_PREVIEW_HTML,
    MAP_PREVIEW_SCRIPT,
    MAP_PREVIEW_STYLE,
)


def test_preview_assets_keep_expected_dom_contract() -> None:
    assert "#lot-preview" in MAP_PREVIEW_STYLE or ".lot-preview" in MAP_PREVIEW_STYLE
    assert 'id="lot-preview"' in MAP_PREVIEW_HTML
    assert 'id="lot-preview-photo"' in MAP_PREVIEW_HTML
    assert 'id="map-status-summary"' in MAP_PREVIEW_HTML
    assert "window.showLotPreview = showLotPreview;" in MAP_PREVIEW_SCRIPT
    assert "bankrotaiBridge.setReviewStatus" in MAP_PREVIEW_SCRIPT


def test_original_gui_exports_remain_static_imports() -> None:
    source = (Path(__file__).resolve().parents[1] / "src/bankrotai/gui.py").read_text(
        encoding="utf-8-sig"
    )
    for name in ("MAP_PREVIEW_STYLE", "MAP_PREVIEW_HTML", "MAP_PREVIEW_SCRIPT"):
        assert f"{name} as {name}" in source
    assert 'MAP_PREVIEW_STYLE = """' not in source
    assert 'MAP_PREVIEW_HTML = """' not in source
    assert 'MAP_PREVIEW_SCRIPT = """' not in source
