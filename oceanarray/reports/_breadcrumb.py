"""The breadcrumb trail a report page shows above its masthead title.

The caller of :func:`oceanarray.report` supplies the trail of ancestor crumbs
(``breadcrumb=[(text, href), …]``, each ``href`` relative to the report root), and
each page appends its own position. :func:`page_crumbs` rebases the ancestor hrefs
for the page's depth under the report root and appends the page's own crumbs; it is
local to oceanarray (the markup matches ctdcast's ``.breadcrumb`` so it can be
vendored later).
"""

from __future__ import annotations

Crumb = tuple[str, str | None]


def page_crumbs(
    ancestors: list[Crumb] | None,
    prefix: str,
    own: list[Crumb],
) -> list[Crumb]:
    """Return a page's breadcrumb: *ancestors* rebased by *prefix*, then *own*.

    Parameters
    ----------
    ancestors : list of (str, str or None) or None
        The caller's trail of ancestor crumbs; each ``href`` is relative to the
        report root. ``None`` or empty means no ancestors (the page shows only its
        own crumbs).
    prefix : str
        The page's relative path back to the report root (``""`` for a page at the
        root, ``"../"`` for one a directory below). Prepended to each ancestor's
        ``href``; a crumb with ``href=None`` is left as plain text.
    own : list of (str, str or None)
        The page's own crumbs, hrefs already relative to the page itself (not the
        report root). The last is usually the current page, with ``href=None``.

    Returns
    -------
    list of (str, str or None)
        The crumbs to render, ancestors first.
    """
    crumbs: list[Crumb] = [
        (text, prefix + href if href else None) for text, href in (ancestors or [])
    ]
    crumbs += own
    return crumbs


def crumbs_from_ctx(
    ctx: dict,
    own: list[Crumb],
    prefix: str = "../",
) -> list[Crumb]:
    """Build a mooring page's breadcrumb from the ancestors stored in *ctx*.

    The mooring pages carry the caller's ancestor crumbs under
    ``ctx["_breadcrumb_ancestors"]`` (set once in ``MooringReport.generate``); each
    page passes its *own* crumbs and the shared *prefix* (``"../"`` — the pages sit
    one directory below the report root). With no ancestors the page shows no trail.

    Parameters
    ----------
    ctx : dict
        The page context; reads ``ctx["_breadcrumb_ancestors"]``.
    own : list of (str, str or None)
        The page's own crumbs (see :func:`page_crumbs`).
    prefix : str, optional
        The page's relative path back to the report root; defaults to ``"../"``.

    Returns
    -------
    list of (str, str or None)
        The crumbs to render, or an empty list when no ancestors were given.
    """
    ancestors = ctx.get("_breadcrumb_ancestors")
    return page_crumbs(ancestors, prefix, own) if ancestors else []
