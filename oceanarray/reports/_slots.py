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
    """Render *draw* at the width of *slot* (``width_in``) and return the base64 PNG.

    Parameters
    ----------
    draw : callable
        A ``draw_*`` function accepting ``width_in`` and returning a Figure or None.
    *args, **kwargs
        Forwarded to *draw*.
    slot : str, optional
        A key of :data:`report_tokens.SLOTS`.
    optional : bool, optional
        Passed to the encoder.

    Returns
    -------
    str or None
        None when *draw* returned None or raised.
    """
    width_in = report_tokens.SLOTS[slot][1]
    return _figdebug.render_b64(
        draw, *args, width_in=width_in, optional=optional, **kwargs
    )
