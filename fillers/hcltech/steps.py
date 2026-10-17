from core.helpers import check, click, fill, pick, upload, wait_for


def step_01_click_apply(page, p):
    click(page, "Apply now", expect="navigation")
