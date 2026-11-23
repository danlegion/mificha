"""Typography: the approved design uses Newsreader only for page titles.

Body and every other heading — family names, doc titles, stat numbers,
tabs, table numbers — are Plus Jakarta Sans, at the design's pixel
sizes. The port once put the serif on every h1/h2/h3 and sized the
small text in rems computed at 16px while the body is 17px, so the
cards read differently from the design. These assertions lock the
scale to the screenshot.
"""

import re


def _css():
    return open("static/app.css").read()


def _rule(css, selector):
    """The declaration block for `selector`, or None."""
    m = re.search(re.escape(selector) + r"\s*\{([^}]*)\}", css)
    return m.group(1) if m else None


def test_serif_only_on_page_titles():
    css = _css()
    # No global serif on every heading.
    global_rule = _rule(css, "h1, h2, h3")
    assert global_rule is not None
    assert "font-family" not in global_rule
    # Serif lives on exactly the four page titles.
    assert css.count("font-family: var(--font-display)") == 4
    for selector in (".hero h1", ".section-head h2", ".vault-head h2",
                     ".detail-hero h1"):
        assert "var(--font-display)" in _rule(css, selector)


def test_family_cards_use_body_font_at_design_sizes():
    css = _css()
    name = _rule(css, ".family-name")
    assert "var(--font-body)" in name
    assert "font-size: 16px" in name
    note = _rule(css, ".family-note")
    assert "font-size: 13px" in note
    assert "line-height: 1.45" in note
    assert "font-size: 12.5px" in _rule(css, ".family-meta")
    assert "font-size: 16px" in _rule(css, ".initials-tile")


def test_hero_stats_and_header_match_design():
    css = _css()
    num = _rule(css, ".stat-num")
    assert "var(--font-body)" in num
    assert "font-size: 22px" in num
    assert "font-size: 12.5px" in _rule(css, ".stat-label")
    assert "font-size: 20px" in _rule(css, ".wordmark")
    assert "font-size: 14px" in _rule(css, ".site-nav")


def test_vault_type_matches_design():
    css = _css()
    tabs = _rule(css, ".vault-tabs button")
    assert "font-size: 14px" in tabs
    assert "font-weight: 700" in tabs
    assert "font-size: 13.5px" in _rule(css, ".member-pill")
    assert "font-size: 14px" in _rule(css, ".vault-search input")
    assert "font-size: 14px" in _rule(css, ".vault-head p")
    assert "font-size: 14px" in _rule(css, ".section-sub")


def test_doc_cards_and_vaccines_match_design():
    css = _css()
    title = _rule(css, ".doc-about")
    assert "var(--font-body)" in title
    assert "font-size: 16px" in title
    assert "line-height: 1.25" in title
    assert "font-size: 13px" in _rule(css, ".doc-who")
    assert "font-size: 13.5px" in _rule(css, ".doc-summary")
    assert "font-size: 11.5px" in _rule(css, ".tag")
    assert "font-size: 12px" in _rule(css, ".doc-source")
    head = _rule(css, ".vax-group-head h2")
    assert "var(--font-body)" in head
    assert "font-size: 14px" in head
    assert "font-size: 14.5px" in _rule(css, ".vax-main strong")
    assert "font-size: 13px" in _rule(css, ".vax-main span")
    assert "font-size: 11px" in _rule(css, ".vax-badge")


def test_detail_hero_matches_design():
    css = _css()
    assert "font-size: 15px" in _rule(css, ".detail-plain p")
    assert "font-size: 14px" in _rule(css, ".kv-top")
    assert "font-size: 13px" in _rule(css, ".detail-meta")


def test_upload_card_title_is_sans():
    css = _css()
    title = _rule(css, ".hero-upload .upload-card h2")
    assert "var(--font-body)" in title
    assert "font-size: 18px" in title
    assert "font-size: 14px" in _rule(css, ".hero-upload .upload-lead")
