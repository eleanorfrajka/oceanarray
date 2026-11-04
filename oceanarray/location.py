"""Geographic position of a mooring: parse and format latitude and longitude."""

from __future__ import annotations


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


LATLON_UNKNOWN_SOURCE = "unknown (defaulting to 0, 0)"


def parse_latlon(cfg: dict) -> tuple[float, float]:
    """Return (lat, lon) in decimal degrees from a mooring config dict.

    Parameters
    ----------
    cfg : dict
        Mooring configuration dictionary (from a ``.mooring.yaml`` file).

    Returns
    -------
    tuple[float, float]
        ``(latitude, longitude)`` in decimal degrees.

    """
    lat, lon, _ = parse_latlon_with_source(cfg)
    return lat, lon


def is_unknown_latlon_source(source: str) -> bool:
    """Return True if *source* is the no-position sentinel from :func:`parse_latlon_with_source`."""
    return source == LATLON_UNKNOWN_SOURCE


def parse_latlon_with_source(cfg: dict) -> tuple[float, float, str]:
    """Return (lat, lon, source_key) from a mooring config dict.

    source_key is the YAML key pair used, e.g. ``'seabed_latitude/seabed_longitude'``,
    or :data:`LATLON_UNKNOWN_SOURCE` if none found.

    Zero values (lat == 0 and lon == 0) are treated as unfilled placeholders and
    skipped so that a later key with a real location is used instead.

    Parameters
    ----------
    cfg : dict
        Mooring configuration dictionary (from a ``.mooring.yaml`` file).

    Returns
    -------
    tuple[float, float, str]
        ``(latitude, longitude, source_key)`` where source_key identifies which
        YAML key pair was used.

    """
    for lat_key, lon_key in [
        ("seabed_latitude", "seabed_longitude"),
        ("deployment_latitude", "deployment_longitude"),
        ("planned_latitude", "planned_longitude"),
        ("latitude", "longitude"),
    ]:
        lat_s = cfg.get(lat_key)
        lon_s = cfg.get(lon_key)
        if lat_s is not None and lon_s is not None:
            try:
                lat_v = _parse_dms(str(lat_s))
                lon_v = _parse_dms(str(lon_s))
            except Exception:  # noqa: BLE001  — _parse_dms parse failure; try next key
                continue
            if lat_v == 0.0 and lon_v == 0.0:
                continue  # treat (0, 0) as an unfilled placeholder
            return lat_v, lon_v, f"{lat_key}/{lon_key}"
    return 0.0, 0.0, LATLON_UNKNOWN_SOURCE
