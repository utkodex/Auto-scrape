from core.helpers import click


def step_1_click_apply(page, p):
    click(page, "Apply", expect="navigation")
