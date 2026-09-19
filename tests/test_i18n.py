import re
from pathlib import Path


STATIC = Path(__file__).parents[1] / "workout_relay" / "static"


def _dictionary_keys(source: str, language: str) -> set[str]:
    if language == "en":
        section = source.split("en: {", 1)[1].split("\n  },\n  fr: {", 1)[0]
    else:
        section = source.split("\n  fr: {", 1)[1].split("\n  }\n};", 1)[0]
    return set(
        re.findall(r"(?:^|,\s*)\s*([A-Za-z][A-Za-z0-9_]*):", section, re.MULTILINE)
    )


def test_every_declared_ui_string_exists_in_both_languages():
    html = (STATIC / "index.html").read_text()
    script = (STATIC / "app.js").read_text()
    html_keys = set(re.findall(r'data-i18n(?:-placeholder|-aria)?="([^"]+)"', html))
    literal_dynamic_keys = set(re.findall(r'\bt\("([A-Za-z0-9_]+)"', script))
    english = _dictionary_keys(script, "en")
    french = _dictionary_keys(script, "fr")
    assert english == french
    assert html_keys <= english
    assert literal_dynamic_keys <= english
