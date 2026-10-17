import os

from core.helpers import click, fill


def step_01_click_apply(page, p):
    click(page, "Apply", expect="navigation")
    fill(page, "First Name", os.environ["X"])
