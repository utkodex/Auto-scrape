from urllib.request import urlopen

import pytest

PAGES = {
    "job.html": ["Apply", "apply-slot", "Engineer"],
    "apply_1.html": ["First Name", "Last Name", "Email", "Country Code", "Phone Number", "Resume", "Next"],
    "apply_2.html": ["Country", "City", "Willing to relocate", "Work authorization in India",
                     "Notice period (days)", "Why do you want to join?", "Next"],
    "review.html": ["Submit application"],
    "login.html": ["Password", "Sign in"],
    "submitted.html": ["Submitted"],
    "ambiguous.html": ["Phone"],
    "common.js": ["requireLogin", "validate"],
}


@pytest.mark.parametrize("page,needles", PAGES.items())
def test_fixture_pages_serve(fixture_server, page, needles):
    with urlopen(fixture_server + page) as resp:
        assert resp.status == 200
        body = resp.read().decode("utf-8")
    for needle in needles:
        assert needle in body, f"{page} lacks {needle!r}"


def test_query_string_ignored_by_server(fixture_server):
    with urlopen(fixture_server + "job.html?id=2&popup=1") as resp:
        assert resp.status == 200
