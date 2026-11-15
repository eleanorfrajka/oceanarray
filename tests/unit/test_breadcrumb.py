"""The report breadcrumb helpers rebase ancestor hrefs and append a page's own crumbs."""

from oceanarray.reports._breadcrumb import crumbs_from_ctx, page_crumbs


def test_page_crumbs_rebases_ancestor_hrefs_and_appends_own():
    out = page_crumbs(
        [("Index", "index.html"), ("Site", "site.html")], "../", [("egM2", None)]
    )
    assert out == [
        ("Index", "../index.html"),
        ("Site", "../site.html"),
        ("egM2", None),
    ]


def test_page_crumbs_leaves_an_hrefless_ancestor_as_plain_text():
    assert page_crumbs([("Index", None)], "../", []) == [("Index", None)]


def test_page_crumbs_no_prefix_at_the_report_root():
    assert page_crumbs([("Index", "index.html")], "", []) == [("Index", "index.html")]


def test_page_crumbs_deeper_page_prefix():
    assert page_crumbs([("Index", "index.html")], "../../", [("9920", None)]) == [
        ("Index", "../../index.html"),
        ("9920", None),
    ]


def test_crumbs_from_ctx_uses_the_stored_ancestors():
    ctx = {"_breadcrumb_ancestors": [("Index", "index.html")]}
    assert crumbs_from_ctx(ctx, [("m", None)]) == [
        ("Index", "../index.html"),
        ("m", None),
    ]


def test_crumbs_from_ctx_is_empty_without_ancestors():
    assert crumbs_from_ctx({}, [("m", None)]) == []
    assert crumbs_from_ctx({"_breadcrumb_ancestors": None}, [("m", None)]) == []
