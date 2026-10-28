import os

import pytest

from melograb import template
from melograb.track import Track


def t(**kw):
    base = dict(title="Husk", artist="Men I Trust", album="Oncle Jazz", track_number=7, release_date="2019-11-08",
                playlist="Chill", position=12)
    base.update(kw)
    return Track(**base)


@pytest.mark.parametrize(
    "tmpl,expected",
    [
        (template.DEFAULT_TEMPLATE, "Oncle Jazz/Men I Trust - Husk"),
        (template.FLAT_TEMPLATE, "Men I Trust - Husk"),
        ("{album_artist}/{album}/{track:02} {title}", "Men I Trust/Oncle Jazz/07 Husk"),
        ("{playlist}/{position:03} {artist} - {title}", "Chill/012 Men I Trust - Husk"),
        ("{year}/{title}", "2019/Husk"),
    ],
)
def test_render(tmpl, expected):
    assert template.render(tmpl, t()) == expected.replace("/", os.sep)


def test_slash_in_values_never_makes_folders():
    path = template.render(template.DEFAULT_TEMPLATE, t(album="A/B", title="x/y", artist="AC/DC"))
    assert path == os.path.join("A-B", "AC-DC - x-y")


def test_missing_values_have_fallbacks():
    path = template.render("{album_artist}/{album}/{title}", Track(title="Song", artist="A, B"))
    assert path == os.path.join("A", "Unknown Album", "Song")


@pytest.mark.parametrize(
    "tmpl,msg",
    [
        ("", "empty"),
        ("/abs/{title}", "relative"),
        ("{album}/../{title}", "'..'"),
        ("{album}//{title}", "empty"),
        ("{title}/{artist}", "must include {title}"),
        ("{nope} {title}", "unknown field {nope}"),
        ("{title:zz}", "invalid template"),
    ],
)
def test_validate_rejects(tmpl, msg):
    with pytest.raises(ValueError, match=msg.replace("{", r"\{").replace("}", r"\}").replace(".", r"\.")):
        template.validate(tmpl)


def test_presets_are_valid():
    for name, value in template.PRESETS.items():
        template.validate(value)
        assert template.preset_name(value) == name
