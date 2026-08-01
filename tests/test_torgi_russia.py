from bankrotai.torgi_russia import TorgiRussiaClient


def test_torgi_russia_search_requires_matching_cadastral_number() -> None:
    html = """
    <article><a href="/lot/111">First</a><p>76:02:000000:1</p></article>
    <article><a href="/lot/6884641">Second</a><p>76:02:071501:198</p></article>
    """

    assert TorgiRussiaClient._matching_lot_url(html, "76:02:071501:198").endswith("/lot/6884641")
    assert TorgiRussiaClient._matching_lot_url(html, "76:02:071501:999") is None


def test_torgi_russia_lot_page_parses_gallery_and_related_links() -> None:
    html = """
    <div id="lot-gallery" data-gallery='[
      {"url":"/pictures/one.jpg","thumb_url":"/thumb/one.jpg"},
      {"url":"https://cdn.example/two.jpg"}
    ]'></div>
    <a href="https://catalog.lot-online.ru/notice/21000002210000009602/1">\u0422\u043e\u0440\u0433\u0438 \u043d\u0430 \u042d\u0422\u041f</a>
    <a href="https://torgi.gov.ru/new/public/lots/lot/example/(lotInfo:info)">\u041b\u043e\u0442 \u043d\u0430 \u0413\u0418\u0421 \u0422\u043e\u0440\u0433\u0438</a>
    """

    result = TorgiRussiaClient.parse_lot_page(
        html,
        "https://xn----etbpba5admdlad.xn--p1ai/lot/6884641",
    )

    assert result.torgi_russia_url.endswith("/lot/6884641")
    assert result.etp_url == "https://catalog.lot-online.ru/notice/21000002210000009602/1"
    assert result.gis_torgi_url.endswith("/(lotInfo:info)")
    assert result.image_urls == [
        "https://xn----etbpba5admdlad.xn--p1ai/pictures/one.jpg",
        "https://cdn.example/two.jpg",
    ]
