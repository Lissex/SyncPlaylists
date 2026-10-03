"""Реальные названия SoundCloud (публичный поиск api-v2, 2026-10-03): мусорные теги
загрузчиков и DJ-версии (bootleg/edit/rmx/flip/vip), которые нельзя путать с оригиналом."""

import pytest

from syncplaylists.modules.matching.domain.normalization import TrackNormalizer
from syncplaylists.modules.matching.domain.version import VersionTag, extract_version


@pytest.mark.parametrize(
    ("raw", "artist", "title"),
    [
        ("Wolverave & Robbe - DRUGS [FREE DL]", "wolverave & robbe", "drugs"),
        ("Blotter - Getting Hot [Free DL]", "blotter", "getting hot"),
        (
            "FREE DL | Girl in Red - I wanna be your girlfriend",
            "girl in red",
            "i wanna be your girlfriend",
        ),
        ("Tyla - Water (Free Download)", "tyla", "water"),
        ("Arctic Monkeys - 505 [Free Download]", "arctic monkeys", "505"),
        ("Careless - Ryan (FREE D/L)", "careless", "ryan"),
        ("Cassö x Jazzy - Zeros *FREE DOWNLOAD*", "cassö x jazzy", "zeros"),
        ("Imagine Dragons - Shots BUY = Free Download", "imagine dragons", "shots"),
        ("Binz - Sao Cũng Được | Free Download", "binz", "sao cũng được"),
        ("Kuko - Tageslicht - - FREE DOWNLOAD - -", "kuko", "tageslicht"),
        ("Eminem - Lose Yourself 320kbps", "eminem", "lose yourself"),
        ("Premiere: Bicep - Glue", "bicep", "glue"),
        ("Bicep - Glue [PREMIERE]", "bicep", "glue"),
        ("Bicep - Glue (Out Now on Ninja Tune)", "bicep", "glue"),
        ("Bicep - Glue (Official Audio)", "bicep", "glue"),
        ("Bicep - Glue [Official Music Video]", "bicep", "glue"),
        ("YEAH - Usher [PREVIEW]", "yeah", "usher"),
        ("FBLO - Bleed the Freak #SPRÄNGAMIDDAR", "fblo", "bleed the freak"),
        ("PARACHUTE - PARYS | OFFICIAL AUDIO", "parachute", "parys"),
        ("Lil Durk - Delete My Number Official Audio", "lil durk", "delete my number"),
        ("Ka-Re - Половина моя | OFFICIAL AUDIO 2018 |", "ka-re", "половина моя"),
        ("French Montana - Unforgettable (Official Audio) [HQ]", "french montana", "unforgettable"),
        ("Philip George - Alone No More OUT NOW", "philip george", "alone no more"),
        ("Sommerregen - Nimm meine Hand - OUT NOW on Spotify", "sommerregen", "nimm meine hand"),
        ("Reparations - KA$HDAMI [Music Video Out Now!]", "reparations", "ka$hdami"),
        ("Immaculate - Visxge {OUT NOW ON ALL PLATFORMS}", "immaculate", "visxge"),
        ("PREMIERE060: SMVGGLERS x KØDA - ME FLIPA", "smvgglers x køda", "me flipa"),
        ("PREMIERE /// Jonathan Ross - Fallin'", "jonathan ross", "fallin'"),
        ("Phil Berg - Dārin [MR047]", "phil berg", "dārin"),
        ("Lil Peep - nuts [ft. lil skil] (prod. willie g)", "lil peep", "nuts"),
        ("KA$HDAMI - Reparations! (prod Milanezie)", "ka$hdami", "reparations!"),
        ("GTG Premiere | Phil Berg — Seiko [MR047]", "phil berg", "seiko"),
        ("TC Premiere: Luis Mendizabal - Smoke", "luis mendizabal", "smoke"),
        ("Premiere: Setaoc Mass - Never Mind [VAULTREC016]", "setaoc mass", "never mind"),
        ("Il Capo - Real Boss (ProdByDxxp)", "il capo", "real boss"),
        ("Ramirez - Grey Gods [Prod.By Tacet]", "ramirez", "grey gods"),
        ("Jah Khalib - Сжигая Дотла (Prod.By Jah Khalib)", "jah khalib", "сжигая дотла"),
    ],
)
def test_uploader_junk_is_removed(raw: str, artist: str, title: str) -> None:
    result = TrackNormalizer().normalize(raw)
    assert (result.artist, result.title) == (artist, title)


def test_mix_title_with_premiere_inside_is_kept() -> None:
    # «Premiere» не в начале строки — не префикс канала.
    result = TrackNormalizer().normalize("Big Mix, Vol. 27: Chicago Concert Premiere - Two Friends")
    assert result.title == "two friends"
    assert result.artist == "big mix, vol. 27: chicago concert premiere"


def test_free_your_mind_is_not_junk() -> None:
    # «Free» в самом названии — не тег загрузчика.
    result = TrackNormalizer().normalize("Prospa - Free Your Mind")
    assert result.title == "free your mind"


@pytest.mark.parametrize(
    ("raw", "remaining", "remixer"),
    [
        (
            "katy perry - firework (andreaaa.zo hardstyle bootleg)",
            "katy perry - firework",
            "andreaaa.zo",
        ),
        (
            "rihanna - don't stop the music (ed marquis bootleg)",
            "rihanna - don't stop the music",
            "ed marquis",
        ),
        ("tom zanetti - you want me (teedee edit)", "tom zanetti - you want me", "teedee"),
        (
            "bad bunny - cafe con ron (no romeo schranz edit)",
            "bad bunny - cafe con ron",
            "no romeo",
        ),
        ("i need your lovin' [danny ores gabber rmx]", "i need your lovin'", "danny ores"),
        (
            "shakira - whenever, wherever (luvego bootleg edit)",
            "shakira - whenever, wherever",
            "luvego",
        ),
        ("locked out of heaven - kaai edit", "locked out of heaven", "kaai"),
        ("free your mind - james hype edit", "free your mind", "james hype"),
        ("bicep - glue (four tet flip)", "bicep - glue", "four tet"),
        ("bicep - glue (bonobo rework)", "bicep - glue", "bonobo"),
    ],
)
def test_dj_versions_are_remixes(raw: str, remaining: str, remixer: str) -> None:
    text, version = extract_version(raw)
    assert text == remaining
    assert version.tag is VersionTag.REMIX
    assert version.remixer == remixer


@pytest.mark.parametrize(
    "raw",
    [
        "revealer - kantelen (bootleg)",
        "slb 2026 bass canyon (vip)",
        "bass canyon [vip mix]",
        "nobody else x come & go (mashup)",
        "you dont even know me bootleg",
    ],
)
def test_bare_dj_versions_are_remixes_without_remixer(raw: str) -> None:
    _, version = extract_version(raw)
    assert version.tag is VersionTag.REMIX
    assert version.remixer is None


@pytest.mark.parametrize(
    ("raw", "tag"),
    [
        ("starboy (radio edit)", VersionTag.RADIO_EDIT),
        ("starboy (extended edit)", VersionTag.EXTENDED),
        ("starboy (clean edit)", VersionTag.ORIGINAL),
        ("starboy (explicit edit)", VersionTag.ORIGINAL),
        ("starboy (edit)", VersionTag.ORIGINAL),
        ("edit the world", VersionTag.ORIGINAL),
    ],
)
def test_service_edits_are_not_dj_versions(raw: str, tag: VersionTag) -> None:
    _, version = extract_version(raw)
    assert version.tag is tag


def test_named_remix_remixer_drops_genre_word() -> None:
    _, version = extract_version("haven - i run (bonkers hardstyle remix)")
    assert version.remixer == "bonkers"
