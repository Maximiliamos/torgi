from bankrotai.services.map_view import extract_map_image_urls


def test_map_images_accept_only_public_http_urls_and_remove_duplicates() -> None:
    raw = {
        "photos": [
            {"url": "https://example.test/one.jpg"},
            {"thumbnail": "//example.test/two.jpg"},
            {"image_url": "javascript:alert(1)"},
            {"photo_url": "file:///private/photo.jpg"},
            {"url": "https://example.test/one.jpg"},
        ]
    }

    assert extract_map_image_urls(raw) == [
        "https://example.test/one.jpg",
        "https://example.test/two.jpg",
    ]
