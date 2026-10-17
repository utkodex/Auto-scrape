from core.observe import ObserveResult, approx_tokens, observe


def _kinds(result: ObserveResult) -> dict[str, str]:
    return {f.label: f.kind for f in result.fields}


def test_apply_1_fields_and_options(browser, fixture_server):
    browser.goto(fixture_server + "apply_1.html")
    r = observe(browser)
    assert r.page_kind == "form"
    assert _kinds(r) == {
        "First Name": "text", "Last Name": "text", "Email": "text",
        "Country Code": "combobox", "Phone Number": "text", "Resume": "file",
    }
    cc = r.field("Country Code")
    assert cc.required and cc.options[0] == "India (+91)"
    assert all(f.required for f in r.fields)
    assert "Next" in r.buttons and "Save" in r.buttons
    assert r.suggested_next.action == "fill" and r.suggested_next.targets == ["First Name"]
    assert r.section == "Personal Information"
    assert approx_tokens(r) <= 1500 and not r.truncated


def test_apply_2_kinds(browser, fixture_server):
    browser.goto(fixture_server + "apply_2.html")
    r = observe(browser)
    k = _kinds(r)
    assert k["Country"] == "select" and "India" in r.field("Country").options
    assert k["City"] == "text"
    assert k["Willing to relocate"] == "radio" and r.field("Willing to relocate").options == ["Yes", "No"]
    assert k["Work authorization in India"] == "checkbox"
    assert k["Notice period (days)"] == "text"
    assert k["Why do you want to join?"] == "textarea"
    assert r.field("Willing to relocate").required
    assert not r.field("Work authorization in India").required


def test_job_page_suggests_apply(browser, fixture_server):
    browser.goto(fixture_server + "job.html")
    r = observe(browser)
    assert r.page_kind == "job_description"
    assert r.suggested_next.action == "click" and r.suggested_next.targets == ["Apply"]


def test_phone_pair_suggested_after_names(browser, fixture_server):
    browser.goto(fixture_server + "apply_1.html")
    browser.page.fill("#first", "A")
    browser.page.fill("#last", "B")
    browser.page.fill("#email", "a@example.com")
    r = observe(browser)
    assert r.suggested_next.action == "fill_group"
    assert r.suggested_next.targets == ["Country Code", "Phone Number"]


def test_next_button_suggested_when_complete(browser, fixture_server):
    browser.goto(fixture_server + "apply_2.html")
    browser.page.select_option("#country", "India")
    browser.page.fill("#city", "Pune")
    browser.page.check("input[name=relocate][value=Yes]")
    r = observe(browser)
    assert r.suggested_next.action == "click" and r.suggested_next.targets == ["Next"]


def test_review_and_login_suggestions(browser, fixture_server):
    browser.goto(fixture_server + "review.html")
    assert observe(browser).suggested_next.action == "stop"
    browser.goto(fixture_server + "login.html")
    assert observe(browser).suggested_next.action == "ask_human"


def test_many_fields_truncates_under_cap(browser, fixture_server):
    browser.goto(fixture_server + "apply_1.html?many=1")
    r = observe(browser)
    assert r.truncated
    assert approx_tokens(r) <= 1500
    assert len(r.fields) <= 40
    assert r.field("First Name") is not None  # the target survives trimming


def test_scope_narrows_fields(browser, fixture_server):
    browser.goto(fixture_server + "apply_1.html")
    r = observe(browser, scope="Phone")
    assert [f.label for f in r.fields] == ["Phone Number"]


def test_cache_hit_and_file(browser, fixture_server, tmp_path):
    browser.goto(fixture_server + "apply_1.html")
    r1 = observe(browser, cache_dir=tmp_path)
    r2 = observe(browser, cache_dir=tmp_path)
    assert r1.snapshot == r2.snapshot
    assert r2 is r1
    assert (tmp_path / f"{r1.snapshot}.json").exists()


def test_snapshot_changes_when_value_filled(browser, fixture_server):
    browser.goto(fixture_server + "apply_1.html")
    before = observe(browser).snapshot
    browser.page.fill("#first", "A")
    after = observe(browser).snapshot
    assert before != after
