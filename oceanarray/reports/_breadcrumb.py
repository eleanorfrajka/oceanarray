"""The breadcrumb trail a report page shows above its masthead title.

Each page shows its own position by default (``{mooring} › Stack``,
``{site} › Grid cat``, …), the up-link pointing at the page's landing in the same
directory. The caller of :func:`oceanarray.report` may supply a trail of ancestor
crumbs (``breadcrumb=[(text, href), …]``) to sit in front; each ``href`` is relative
to the page's own report directory (the mooring's or site's directory), so the
breadcrumb makes no assumption about where a shared report root lives. The one depth
:func:`page_crumbs` rebases is the instrument subdirectory, a level below its mooring.
It is local to oceanarray (the markup matches ctdcast's ``.breadcrumb`` so it can be
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
        page's own report directory. ``None`` or empty means no ancestors (the page
        shows only its own crumbs).
    prefix : str
        The page's relative path back to its own report directory (``""`` for a page
        in it, ``"../"`` for the instrument subdirectory a level below). Prepended to
        each ancestor's ``href``; a crumb with ``href=None`` is left as plain text.
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
    prefix: str = "",
) -> list[Crumb]:
    """Build a mooring page's breadcrumb from its *own* crumbs and any ancestors.

    The mooring pages carry the caller's ancestor crumbs under
    ``ctx["_breadcrumb_ancestors"]`` (set once in ``MooringReport.generate``); each
    page passes its *own* crumbs, which are always shown, and any ancestors sit in
    front, rebased by *prefix*. The summary, stack and grid pages share one report
    directory, so *prefix* is ``""``.

    Parameters
    ----------
    ctx : dict
        The page context; reads ``ctx["_breadcrumb_ancestors"]``.
    own : list of (str, str or None)
        The page's own crumbs (see :func:`page_crumbs`), always shown.
    prefix : str, optional
        The page's relative path back to its own report directory; defaults to ``""``.

    Returns
    -------
    list of (str, str or None)
        The crumbs to render: the ancestors (if any) then the page's own.
    """
    return page_crumbs(ctx.get("_breadcrumb_ancestors"), prefix, own)
