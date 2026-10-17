from core.helpers import click, fill


def step_01_click_apply(page, p):
    click(page, "Apply", expect="navigation")


def step_02_name(page, p):
    if p.first_name:
        fill(page, "First Name", p.first_name)
