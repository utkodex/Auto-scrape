from core.helpers import check, click, fill, pick, upload, wait_for


def step_01_click_apply(page, p):
    click(page, "Apply", expect="navigation")

def step_02_name(page, p):
    fill(page, "First Name", p.first_name)
    fill(page, "Last Name", p.last_name)

def step_03_email(page, p):
    fill(page, "Email", p.email)

def step_04_phone(page, p):
    pick(page, "Country Code", "India (+91)")
    fill(page, "Phone Number", p.phone)

def step_05_resume(page, p):
    wait_for(page, "Resume")
    upload(page, "Resume", p.resume)

def step_06_next(page, p):
    click(page, "Next", expect="section")

def step_07_country(page, p):
    pick(page, "Country", p.country)

def step_08_city(page, p):
    fill(page, "City", p.city)

def step_09_relocate(page, p):
    check(page, "Willing to relocate", p.answers["willing_to_relocate"])

def step_10_next(page, p):
    click(page, "Next", expect="navigation")
