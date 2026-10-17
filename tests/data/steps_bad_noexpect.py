from core.helpers import click, wait_for


def step_01_click_apply(page, p):
    click(page, "Apply", expect="navigation")


def step_02_wait(page, p):
    wait_for(page, "First Name")
