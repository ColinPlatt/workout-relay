from html.parser import HTMLParser
from pathlib import Path


class UI(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.elements = []
        self.feed(html)

    def handle_starttag(self, tag, attributes):
        self.elements.append((tag, dict(attributes)))


def test_simple_navigation_and_closed_technical_panels():
    html = (Path(__file__).parents[1] / "workout_relay/static/index.html").read_text()
    ui = UI(html)
    sections = [attrs["data-section"] for _, attrs in ui.elements if "data-section" in attrs]
    assert sections == ["upload", "automation", "settings"]
    details = [attrs for tag, attrs in ui.elements if tag == "details"]
    assert len(details) >= 5
    assert all("open" not in attrs for attrs in details)
    ids = [attrs["id"] for _, attrs in ui.elements if "id" in attrs]
    assert len(ids) == len(set(ids))
    assert {"copy-instructions", "copy-connector", "plan-json", "garmin-consent"} <= set(ids)
    descriptions = {attrs["href"] for tag, attrs in ui.elements if tag == "link" and attrs.get("rel") in {"describedby", "service-desc"}}
    assert descriptions == {"/api/v1/plan-format", "/api/v1/plan-schema", "/openapi.json"}


def test_login_has_no_marketing_or_privacy_copy():
    html = (Path(__file__).parents[1] / "workout_relay/static/index.html").read_text()
    login = html.split('id="auth-view"', 1)[1].split('id="dashboard"', 1)[0]
    assert 'id="auth-form"' in login
    for key in ("heroTitle", "heroCopy", "heroEyebrow", "workflowCaption", "trustLine"):
        assert f'data-i18n="{key}"' not in login
    assert 'id="register-hint" class="hint hidden"' in login
    assert '<body class="auth-only">' in html
    assert 'src="/static/runner.svg"' in html


def test_runner_is_a_small_self_contained_vector():
    from xml.etree import ElementTree

    logo = Path(__file__).parents[1] / "workout_relay/static/runner.svg"
    svg = ElementTree.fromstring(logo.read_text())
    assert svg.attrib["viewBox"] == "0 0 64 64"
    assert len(logo.read_bytes()) < 2000
    assert "script" not in logo.read_text() and "href=" not in logo.read_text()
