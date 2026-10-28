from melograb.track import Track, sanitize_filename


def test_sanitize_replaces_slashes_and_strips_illegal():
    assert sanitize_filename("AC/DC: Back in Black?") == "AC-DC Back in Black"


def test_sanitize_strips_trailing_dots_and_whitespace():
    assert sanitize_filename("  Hello...  ") == "Hello"


def test_sanitize_fallback_when_empty():
    assert sanitize_filename('???', fallback="Unknown Album") == "Unknown Album"


def test_sanitize_truncates_long_names_on_utf8_boundary():
    name = sanitize_filename("é" * 500)
    assert len(name.encode("utf-8")) <= 180
    name.encode("utf-8").decode("utf-8")


def test_sanitize_keeps_percent_and_unicode():
    assert sanitize_filename("100% Ünïcödé") == "100% Ünïcödé"


def test_filename_stem_without_artist():
    assert Track(title="Song", artist="").filename_stem == "Song"


def test_album_folder_default():
    assert Track(title="x", artist="y").album_folder_name == "Unknown Album"
