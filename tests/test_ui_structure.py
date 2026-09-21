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
