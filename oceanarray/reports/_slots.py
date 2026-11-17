"""Package-local slot layer: render a figure at the inch-width of its display slot.

A report figure is rendered at the inch-width of the display *slot* the template
places it in, so the PNG pixel width equals the on-page width (no
oversample-then-downscale mismatch).  One slot decision -- taken at the L4 figure
builder in :mod:`oceanarray.reports._plots` -- drives the rendered width; the
section manifest carries the same slot name on the resolved panel (``Panel.slot``)
so the template's ``panel()`` macro emits the matching ``.slot-*`` class.

:func:`render` resolves ``report_tokens.SLOTS[slot]`` to inches, forwards that
width to the draw function as ``width_in``, and delegates the encode to the
figure-debug wrapper (which delegates to the vendored encoder -- its signature is
untouched).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from . import _figdebug
from ._vendored import design_tokens as report_tokens


def render(
    draw: Callable[..., Any],
    /,
    *args: Any,
    slot: str = "full",
    optional: bool = False,
    **kwargs: Any,
) -> str | None:
    """Render *draw* at the inch-width of *slot* and return the base64 PNG.

    Resolves ``report_tokens.SLOTS[slot]`` to an inch width and forwards it to
    *draw* as the ``width_in`` keyword.  The display slot travels with the figure
    through the section manifest (``Panel.slot``), so the caller records the slot
    there rather than in a side table.

    Parameters
    ----------
    draw : callable
        A ``draw_*`` function (or ``_make_*`` closure) that accepts ``width_in``
        and returns a Figure or ``None``.
    *args, **kwargs
        Forwarded to *draw*.
    slot : str
        A key of :data:`report_tokens.SLOTS` (default ``"full"``).
    optional : bool
        Passed through to the encoder (see
        ``reports._encode.render_b64``).

    Returns
    -------
    str or None
        Base64-encoded PNG, or ``None`` when *draw* returned ``None`` or raised.

    """
    width_in = report_tokens.SLOTS[slot][1]
    return _figdebug.render_b64(
        draw, *args, width_in=width_in, optional=optional, **kwargs
    )
