"""Geographic position of a mooring: parse and format latitude and longitude."""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any

from oceanarray import _deprecated


def _parse_dms(s: str) -> float:
    """Parse 'DD MM.mmm N/S/E/W', signed 'DD MM.mmm' or a float to decimal degrees."""
    # The sign applies to the whole magnitude: "-27 48.000" is -(27 + 48/60) = -27.8,
    # not -27 + 48/60. A leading minus and a trailing S/W are equivalent markers.
    s = s.strip()
    try:
        return float(s)
    except ValueError:
        pass
    parts = s.upper().split()
    hemi = parts[-1] if parts[-1] in ("N", "S", "E", "W") else None
    nums = parts[:-1] if hemi else parts
    negative = nums[0].startswith("-") or hemi in ("S", "W")
    magnitude = sum(abs(float(x)) / 60.0**i for i, x in enumerate(nums))
    return -magnitude if negative else magnitude


def parse_coordinate(value: Any) -> float | None:
    """Parse one latitude or longitude value to signed decimal degrees.

    Accepts a number, a decimal-degree string (``"-40.2772"``), or degrees and
    decimal minutes with an optional hemisphere letter (``"65 43.913 N"``,
    ``"-27 48.000"``); a leading minus and a trailing ``S``/``W`` are equivalent.

    Parameters
    ----------
    value : Any
        The value as given in a mooring YAML (number or string).

    Returns
    -------
    float or None
        Decimal degrees, or None when the value is absent (None or empty),
        unparseable, or not finite (``nan``, ``inf``).

    """
    if value is None or value == "":
        return None
    try:
        parsed = _parse_dms(str(value))
    except (ValueError, IndexError):
        return None
    return parsed if math.isfinite(parsed) else None


def format_latlon(lat: float, lon: float, *, ndp: int = 4) -> tuple[str, str]:
    """Return (lat, lon) as hemisphere-suffixed strings from signed decimal degrees.

    The sign selects the hemisphere and the magnitude is shown unsigned, so a
    formatted value can never carry both a minus sign and a hemisphere letter
    (e.g. the malformed ``"-27.8 W"``).  Latitude uses ``N``/``S``, longitude
    ``E``/``W``; a value of exactly 0 is rendered on the positive hemisphere.

    Parameters
    ----------
    lat, lon : float
        Latitude and longitude in signed decimal degrees.
    ndp : int, optional
        Number of decimal places for the magnitude (default 4 ≈ 11 m).

    Returns
    -------
    tuple[str, str]
        ``(lat_str, lon_str)``, e.g. ``("65.7319° N", "27.8000° W")``.

    """
    lat_hemi = "N" if lat >= 0 else "S"
    lon_hemi = "E" if lon >= 0 else "W"
    return (
        f"{abs(lat):.{ndp}f}° {lat_hemi}",
        f"{abs(lon):.{ndp}f}° {lon_hemi}",
    )


def format_ddm(value: float, *, is_lat: bool) -> str:
    """Return a position as degrees and decimal minutes with a hemisphere letter.

    The value is rounded once, to the nearest hundredth of a minute, and only then
    split into degrees and minutes, so the minutes never read ``60.00′`` (64.99992
    gives ``65° 00.00′ N``, not ``64° 60.00′ N``). A value that rounds to zero takes
    the positive hemisphere (``N`` or ``E``), never ``S`` or ``W``.

    Parameters
    ----------
    value : float
        Latitude or longitude in signed decimal degrees.
    is_lat : bool
        True for a latitude (two-digit degrees, ``N``/``S``); False for a
        longitude (three-digit degrees, ``E``/``W``).

    Returns
    -------
    str
        For example ``"65° 43.91′ N"`` or ``"027° 48.00′ W"``.

    """
    hundredths = round(abs(value) * 6000)  # whole hundredths of a minute
    deg, hundredths = divmod(hundredths, 6000)
    pos, neg = ("N", "S") if is_lat else ("E", "W")
    hemi = neg if value < 0 and (deg or hundredths) else pos
    return f"{deg:0{2 if is_lat else 3}d}° {hundredths / 100:05.2f}′ {hemi}"


LATLON_UNKNOWN_SOURCE = "unknown (defaulting to 0, 0)"

#: Position key pairs of a mooring YAML, in the order they are tried.
LATLON_KEY_PAIRS: tuple[tuple[str, str], ...] = (
    ("seabed_latitude", "seabed_longitude"),
    ("deployment_latitude", "deployment_longitude"),
    ("planned_latitude", "planned_longitude"),
    ("latitude", "longitude"),
)


def parse_latlon(
    mooring_dict: Mapping | None = None, *, cfg: Mapping | None = None
) -> tuple[float, float]:
    """Return (lat, lon) in decimal degrees from a loaded mooring YAML.

    Parameters
    ----------
    mooring_dict : Mapping
        The loaded mooring YAML (a ``*.mooring.yaml`` file).
    cfg : Mapping, optional
        Deprecated name of *mooring_dict*; removed in 0.8.0.

    Returns
    -------
    tuple[float, float]
        ``(latitude, longitude)`` in decimal degrees.

    """
    mooring_dict = _deprecated.resolve_keyword(
        "oceanarray.mooring.location.parse_latlon(cfg=)",
        mooring_dict,
        cfg,
        required="mooring_dict",
    )
    lat, lon, _ = parse_latlon_with_source(mooring_dict)
    return lat, lon


def is_unknown_latlon_source(source: str) -> bool:
    """Return True if *source* is the no-position sentinel from :func:`parse_latlon_with_source`."""
    return source == LATLON_UNKNOWN_SOURCE


def parse_latlon_with_source(
    mooring_dict: Mapping | None = None, *, cfg: Mapping | None = None
) -> tuple[float, float, str]:
    """Return (lat, lon, source_key) from a loaded mooring YAML.

    source_key is the YAML key pair used, e.g. ``'seabed_latitude/seabed_longitude'``,
    or :data:`LATLON_UNKNOWN_SOURCE` if none found.

    Latitude and longitude always come from the same key pair. Zero values
    (lat == 0 and lon == 0) are treated as unfilled placeholders, and a pair with
    a value that does not parse or is not finite (``nan``) as unknown; either way
    the pair is skipped so that a later pair with a real location is used instead.

    Parameters
    ----------
    mooring_dict : Mapping
        The loaded mooring YAML (a ``*.mooring.yaml`` file), or any mapping with
        its position keys (a product's attributes).
    cfg : Mapping, optional
        Deprecated name of *mooring_dict*; removed in 0.8.0.

    Returns
    -------
    tuple[float, float, str]
        ``(latitude, longitude, source_key)`` where source_key identifies which
        YAML key pair was used.

    """
    mooring_dict = _deprecated.resolve_keyword(
        "oceanarray.mooring.location.parse_latlon_with_source(cfg=)",
        mooring_dict,
        cfg,
        required="mooring_dict",
    )
    for lat_key, lon_key in LATLON_KEY_PAIRS:
        lat_v = parse_coordinate(mooring_dict.get(lat_key))
        lon_v = parse_coordinate(mooring_dict.get(lon_key))
        if lat_v is None or lon_v is None:
            continue  # absent, unparseable or non-finite: try the next pair
        if lat_v == 0.0 and lon_v == 0.0:
            continue  # treat (0, 0) as an unfilled placeholder
        return lat_v, lon_v, f"{lat_key}/{lon_key}"
    return 0.0, 0.0, LATLON_UNKNOWN_SOURCE
