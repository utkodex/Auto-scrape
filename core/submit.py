"""The only code that may press Submit.

Guarded: refuses unless MODE == "live" and the caller passes the human confirmation token.
Nothing under agent/ or core/ imports this module in v1 (tests/test_submit_isolation.py).
"""

from __future__ import annotations

from core.browser import SUBMIT_RE
from core.helpers import ForbiddenAction

CONFIRM_TOKEN = "HUMAN-CONFIRMED"


def submit(page, confirm_token: str) -> None:
    from agent.config import Settings

    settings = Settings()
    if settings.MODE != "live":
        raise ForbiddenAction(f"submit is not allowed in MODE={settings.MODE}")
    if confirm_token != CONFIRM_TOKEN:
        raise ForbiddenAction("submit requires the human confirmation token")
    # Live submission is a later spec; the guard above is the whole point of this file in v1.
    buttons = page.get_by_role("button")
    for i in range(buttons.count()):
        text = buttons.nth(i).inner_text().strip()
        if SUBMIT_RE.match(text):
            raise NotImplementedError("live submit is not implemented in v1")
    raise ForbiddenAction("no submit button found")
