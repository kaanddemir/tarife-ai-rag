from app.rag.retrieve import _detect_gb, _detect_max_price, _detect_operator


def test_detect_operator():
    assert _detect_operator("Turkcell paketleri neler?") == "turkcell"
    assert _detect_operator("türk telekom 20 gb") == "turk_telekom"
    assert _detect_operator("vodafone kolay paket") == "vodafone"
    assert _detect_operator("en ucuz paket") is None


def test_detect_max_price():
    assert _detect_max_price("200 TL altindaki paketler") == 200.0
    assert _detect_max_price("en fazla 150 lira") == 150.0
    assert _detect_max_price("150 liraya kadar") == 150.0
    assert _detect_max_price("20 GB paketleri karsilastir") is None


def test_detect_gb():
    assert _detect_gb("20 GB paketleri karsilastir") == 20.0
    assert _detect_gb("ogrenciler icin en iyi paket") is None
