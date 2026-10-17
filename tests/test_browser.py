from core.browser import PageKind


def test_classify_job_description(browser, fixture_server):
    browser.open(fixture_server + "job.html")
    browser.page.wait_for_selector("button:has-text('Apply')")
    assert browser.classify() == PageKind.job_description


def test_classify_form_login_review(browser, fixture_server):
    browser.open(fixture_server + "apply_1.html")
    assert browser.classify() == PageKind.form
    browser.open(fixture_server + "login.html")
    assert browser.classify() == PageKind.login
    browser.open(fixture_server + "review.html")
    assert browser.classify() == PageKind.review


def test_popup_apply_switches_current_page(browser, fixture_server):
    browser.goto(fixture_server + "job.html?popup=1")
    page = browser.page
    page.wait_for_selector("button:has-text('Apply')")
    with page.context.expect_page() as new_page_info:
        page.click("button:has-text('Apply')")
    new_page = new_page_info.value
    new_page.wait_for_load_state()
    assert browser.page is new_page
    assert "apply_1.html" in browser.page.url
    assert "popup=1" in browser.page.url
    # replay semantics: goto closes the popup and returns to the primary page
    browser.goto(fixture_server + "job.html")
    assert len(browser.pages) == 1 and browser.page.url.endswith("job.html")


def test_host_allowlist_blocks_other_hosts(browser, fixture_server):
    browser.goto(fixture_server + "apply_1.html")
    before = browser.blocked_requests
    browser.page.evaluate(
        "() => fetch('http://127.0.0.2:1/x').then(() => 'ok').catch(() => 'blocked')"
    )
    assert browser.blocked_requests == before + 1
    assert browser.blocked[-1].startswith("http://127.0.0.2:1/")
    assert browser.host_allowed("127.0.0.1")
    assert not browser.host_allowed("evil.example")


def test_host_pattern_matching(browser):
    browser.allowed_hosts = ["*.successfactors.com", "careers.hcltech.com"]
    assert browser.host_allowed("rmkcdn.successfactors.com")
    assert browser.host_allowed("successfactors.com")
    assert browser.host_allowed("careers.hcltech.com")
    assert not browser.host_allowed("hcltech.com")
    browser.allowed_hosts = []


def test_detect_system(browser, fixture_server):
    browser.goto(fixture_server + "apply_1.html")
    browser.seen_requests.clear()
    assert browser.detect_system() == "unknown"
    browser.goto(fixture_server + "talentcommunity/apply/1/?locale=en_US")
    assert browser.detect_system() == "successfactors"


def test_screenshot_writes_png(browser, fixture_server, tmp_path):
    browser.goto(fixture_server + "review.html")
    path = browser.screenshot(tmp_path / "shots")
    assert path.exists() and path.stat().st_size > 0


def test_apply_now_is_job_page_not_review(browser, fixture_server):
    # SuccessFactors job pages carry an "Apply now" button that opens the form; it must
    # never be read as a submit button (which turned the job page into "review").
    browser.open(fixture_server + "job.html")
    probe = browser.probe()
    probe["buttons"] = ["Country", "Americas", "Apply now"]
    probe["labelled"] = 0
    assert browser.classify(probe=probe) == PageKind.job_description
    probe["buttons"] = ["Submit application"]
    assert browser.classify(probe=probe) == PageKind.review


def test_closed_posting_is_classified_closed(browser, fixture_server):
    # HCLTech redirects Apply now for an unposted job to "/" with this sentence and no form.
    browser.open(fixture_server + "job.html")
    probe = browser.probe()
    probe.update(buttons=["Country", "Americas"], links=[], labelled=0,
                 body_text="You can't view this job because it's not available at this time.")
    assert browser.classify(probe=probe) == PageKind.closed
    probe["labelled"] = 3  # a real form on the page wins over the sentence
    assert browser.classify(probe=probe) == PageKind.form


def test_apply_link_beats_site_search_box(browser, fixture_server):
    # www.capgemini.com job pages: one optional "location" search field, the call to action is a
    # plain link "Apply for this job". That is a job page, not a form.
    browser.open(fixture_server + "job.html")
    probe = browser.probe()
    probe.update(buttons=["India | EN", "Search"], links=["Apply for this job"], labelled=1, required_empty=0)
    assert browser.classify(probe=probe) == PageKind.job_description
    probe.update(required_empty=1)  # a required field waiting → still a form
    assert browser.classify(probe=probe) == PageKind.form
