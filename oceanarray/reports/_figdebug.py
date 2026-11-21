"""Per-figure debug capture for report pages (opt-in via an env var).

When ``OCEANARRAY_REPORT_DEBUG`` is set (``1``/``true``/``yes``), every figure
serialised for an HTML report records the draw function that produced it, its
matplotlib ``figsize`` (inches), and the resulting PNG pixel size.  Templates
read this back through the Jinja global :func:`figdbg` and print a ``.debug``
section next to each figure — the template supplies the display *slot* it chose,
so the two can be eyeballed against each other (the figsize width should equal
the slot width; a mismatch is the bug this view exists to surface).

The capture is keyed by the base64 PNG string (unique per figure), so a template
looks up exactly the figure it is placing.  The heavy lifting — style, dpi,
canvas — stays in the vendored encoder (:mod:`oceanarray.reports._encode`); this
module only wraps it to record metadata and is a no-op when debug is off.
"""

from __future__ import annotations

import contextlib
import contextvars
import os
from typing import TYPE_CHECKING, Any

from ._vendored import design_tokens as report_tokens
from ._vendored import figure_png as _encode
from ._vendored.page_model import Stub

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

#: Captured metadata, keyed by the figure's base64 PNG string.
_COLLECTED: dict[str, dict[str, Any]] = {}

#: Active panel-capture sink, or None outside an :func:`as_panel` scope.  When set,
#: :func:`render_b64` (given no explicit ``on_error``) records a failed draw's cause here.
_panel_capture: contextvars.ContextVar[dict[str, str] | None] = contextvars.ContextVar(
    "panel_capture", default=None
)


def enabled() -> bool:
    """Return whether report figure-debug capture is switched on.

    Reads ``OCEANARRAY_REPORT_DEBUG``; true for ``"1"``, ``"true"``, ``"yes"``
    (case-insensitive).
    """
    return os.environ.get("OCEANARRAY_REPORT_DEBUG", "").lower() in (
        "1",
        "true",
        "yes",
    )


def record(b64: str | None, func: str, fig: Any) -> None:
    """Record ``(func, figsize, png_px)`` for figure *b64* when debug is on.

    Parameters
    ----------
    b64 : str or None
        The figure's base64 PNG string (the template's lookup key).  Ignored
        when falsy.
    func : str
        A label for the code that drew the figure (draw function name, or a
        ``"_combined[var]"``-style tag).
    fig : matplotlib.figure.Figure
        The figure, read for its size in inches.

    """
    if not enabled() or not b64 or fig is None:
        return
    try:
        w_in, h_in = (float(v) for v in fig.get_size_inches())
    except Exception:  # noqa: BLE001 - figure size is best-effort debug metadata
        return
    _COLLECTED[b64] = {
        "func": func,
        "figsize_in": (round(w_in, 2), round(h_in, 2)),
        "png_px": (
            round(w_in * report_tokens.FIG_DPI),
            round(h_in * report_tokens.FIG_DPI),
        ),
    }


def _draw_name(draw: Any) -> str:
    """Return a readable name for a draw callable, resolving closures."""
    name = getattr(draw, "__qualname__", None) or getattr(draw, "__name__", None)
    if not name:
        return repr(draw)
    # Nested ``_draw`` closures all share __name__; keep the enclosing
    # ``_make_*`` wrapper name from __qualname__, which identifies the figure.
    if ".<locals>." in name:
        name = name.split(".<locals>.")[0]
    return name


def lookup(b64: str | None) -> dict[str, Any] | None:
    """Return the recorded metadata for figure *b64*, or ``None``."""
    if not b64:
        return None
    return _COLLECTED.get(b64)


def figdbg(b64: str | None) -> str:
    """Return a one-line ``func · figsize · png`` string for figure *b64*.

    Registered as a Jinja global so a template's ``.debug`` section can print
    it beside the slot it chose.  Returns ``""`` when there is nothing recorded
    (e.g. debug off, or the figure failed to render).
    """
    info = lookup(b64)
    if info is None:
        return ""
    w_in, h_in = info["figsize_in"]
    w_px, h_px = info["png_px"]
    return f"{info['func']} · figsize {w_in}×{h_in} in · png {w_px}×{h_px} px"


def clear() -> None:
    """Drop all recorded figure metadata (call at the start of a page build)."""
    _COLLECTED.clear()
    _encode.clear_sources()


def render_b64(
    draw: Any,
    /,
    *args: Any,
    optional: bool = False,
    on_error: Callable[[BaseException], None] | None = None,
    **kwargs: Any,
) -> Any:
    """Wrap the encoder ``render_b64`` to record per-figure debug metadata.

    Delegates to ``reports._encode.render_b64`` and keeps its
    ``str | None`` contract (a failed or empty draw returns ``None``); this wrapper
    never returns a :class:`Stub`.  With no explicit *on_error* and inside an
    :func:`as_panel` scope, a draw failure's cause is recorded in the capture sink so
    ``as_panel`` can build the ``Stub``.  When debug is enabled it also wraps *draw*
    to capture the figure's size and name under the returned base64 string.
    """
    if on_error is None:
        _sink = _panel_capture.get()
        if _sink is not None:

            def on_error(exc: BaseException) -> None:
                _sink["reason"] = f"{type(exc).__name__}: {exc}"

    if not enabled():
        return _encode.render_b64(
            draw, *args, optional=optional, on_error=on_error, **kwargs
        )

    import functools

    captured: dict[str, Any] = {}

    @functools.wraps(draw)  # preserve draw.__name__ so encoder error/warns name it
    def _wrapped(*a: Any, **k: Any) -> Any:
        fig = draw(*a, **k)
        if fig is not None:
            captured["fig"] = fig
            captured["func"] = _draw_name(draw)
        return fig

    b64 = _encode.render_b64(
        _wrapped, *args, optional=optional, on_error=on_error, **kwargs
    )
    if b64 and "fig" in captured:
        record(b64, captured["func"], captured["fig"])
    return b64


@contextlib.contextmanager
def _capture_scope() -> Iterator[dict[str, str]]:
    """Activate a panel-capture sink for the duration of the block, then restore."""
    sink: dict[str, str] = {}
    token = _panel_capture.set(sink)
    try:
        yield sink
    finally:
        _panel_capture.reset(token)


def as_panel(make: Callable[[], str | None]) -> str | Stub | None:
    """Run a single-figure builder; return ``Stub("<ExcType>: <message>")`` if its draw raised and was swallowed.

    A draw that returns None (an absent variable) stays None. *make* must make at most one
    :func:`render_b64` call, and scopes must not nest, or the reason is misattributed.

    Parameters
    ----------
    make : callable
        Zero-argument thunk, typically ``lambda: _make_x_b64(...)``.

    Returns
    -------
    str or Stub or None
    """
    with _capture_scope() as sink:
        result = make()
    if result is None and sink.get("reason"):
        return Stub(sink["reason"])
    return result
