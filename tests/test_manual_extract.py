from retirement.modules.property.sources.manual import _extract_structured

HTML = """
<html><head>
<meta property="og:title" content="Casa in vendita a Locorotondo">
<meta property="og:image" content="https://example.test/photo.jpg">
<script type="application/ld+json">
{"@type":"Product","name":"Trullo ristrutturato",
 "description":"Nel centro storico",
 "offers":{"price":"245000","priceCurrency":"EUR"},
 "geo":{"latitude":40.7543,"longitude":17.3268},
 "address":{"addressLocality":"Locorotondo"}}
</script></head>
<body><p>Superficie 145 mq — prezzo 245.000</p></body></html>
"""


def test_extracts_json_ld_first():
    data = _extract_structured(HTML)
    assert data["title"] == "Trullo ristrutturato"      # JSON-LD wins over og:title
    assert data["price"] == 245000.0
    assert data["municipality"] == "Locorotondo"
    assert data["lat"] == 40.7543
    assert data["image"] == "https://example.test/photo.jpg"
    assert data["size_sqm"] == 145.0


def test_survives_junk_html():
    assert _extract_structured("<html><body>nothing here</body></html>") == {}
