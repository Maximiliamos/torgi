"""Pure desktop preview extraction tests run without PySide6 or a Windows host."""

from pathlib import Path

from bankrotai.services.preview_images import extract_preview_image_url, extract_preview_image_urls


def test_nested_preview_images_keep_order_and_deduplicate() -> None:
    images = extract_preview_image_urls(
        {
            "gallery": [
                "https://cdn.example/1.jpg",
                "//cdn.example/2.jpg",
                "https://cdn.example/1.jpg",
                "javascript:alert(1)",
                "file:///C:/private.txt",
            ],
            "nested": {"image_url": "https://cdn.example/3.jpg"},
        }
    )
    assert images == [
        "https://cdn.example/1.jpg",
        "https://cdn.example/2.jpg",
        "https://cdn.example/3.jpg",
    ]
    assert extract_preview_image_url({"image": images}) == images[0]


def test_non_web_sources_do_not_leak_to_preview() -> None:
    assert extract_preview_image_urls({"photo": "file:///secret"}) == []
    assert extract_preview_image_url({"other": "https://ignored.example"}) is None


def test_gui_retains_original_import_surface() -> None:
    source = (Path(__file__).resolve().parents[1] / "src/bankrotai/gui.py").read_text(
        encoding="utf-8-sig"
    )
    assert "extract_preview_image_urls as extract_preview_image_urls" in source
    assert "extract_preview_image_url as extract_preview_image_url" in source
