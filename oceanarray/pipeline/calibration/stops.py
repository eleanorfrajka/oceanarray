"""Reduce one dip's bottle stops to the number a correction applies, per variable.

caldip gives, for each instrument and bottle stop, the instrument and CTD values and their
difference (``instrument − CTD``). This module chooses which stops count and reduces them to
one offset — or, for conductivity, one Sea-Bird AN31 slope — per dip. Combining the two dips
of a deployment and applying the result happen elsewhere.

Two schemes (the mooring YAML ``caldip.scheme``):

``standard``
    temperature: the offset at the deepest usable stop; conductivity: the AN31 slope through
    the origin over the deep, stable stops; pressure: the mean offset at the stops near the
    deployment pressure.
``rapid``
    reproduces the RAPID MicroCAT processing: temperature and conductivity offsets averaged
    over a deep pressure interval after interpolating the stop differences onto a 20-dbar
    grid; pressure interpolated to the deployment pressure (a cubic in pressure below the
    deepest stop).

Every function works on one instrument's record
(:func:`~oceanarray.pipeline.calibration.caldip.instrument_record`) and returns a
:class:`Reduction` — with ``value`` NaN and a ``reason`` when the dip cannot give one.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import xarray as xr

from oceanarray.pipeline.calibration.caldip import CALDIP_FLAG_OK, CALDIP_FLAGS_USABLE

#: oceanarray's variable name → caldip's short stem.
STEMS: dict[str, str] = {
    "temperature": "temp",
    "conductivity": "cond",
    "pressure": "press",
}

#: Conductivity slope, ``standard``: stops deeper than this count when at least two qualify …
DEEP_STOP_DBAR = 500.0
#: … otherwise stops deeper than this; a stop shallower than this never counts.
SHALLOWEST_STOP_DBAR = 200.0
#: ``standard``: the largest CTD standard deviation at a stop for it to count as stable water,
#: per variable (the CTD's ``ctd_<var>_std``; from caldip schema 2). Pressure has none: its
#: scatter at a stop is ship heave, not the water.
STABLE_CTD_STD: dict[str, float] = {"temperature": 0.002, "conductivity": 0.003}
#: Pressure, ``standard``: stops within this distance of the deployment pressure are averaged;
#: with none, the single nearest stop is used (provisional).
PRESSURE_WINDOW_DBAR = 200.0
#: ``rapid``: the pressure grid the stop differences are interpolated onto.
RAPID_GRID_DBAR = 20.0
#: ``rapid``: RAPID holds the deepest stop's difference constant this far below it.
RAPID_EXTEND_DBAR = 100.0
#: ``rapid``: default width of the deep averaging interval, ending at the deepest stop
#: (provisional; RAPID set it by hand, e.g. 3500–5050 dbar at 26°N).
RAPID_INTERVAL_DBAR = 500.0


@dataclass(frozen=True)
class Reduction:
    """One dip's correction for one instrument variable.

    Parameters
    ----------
    variable : str
        ``"temperature"``, ``"conductivity"`` or ``"pressure"``.
    kind : str
        ``"offset"`` (subtract it) or ``"slope"`` (multiply by it).
    value : float
        The offset (variable units, ``instrument − CTD``) or the slope (dimensionless);
        NaN when the dip gives none.
    rule : str
        The rule that produced it, in words, for the provenance attributes.
    stop_pressures : tuple of float
        CTD pressure (dbar) of each stop used.
    n : int
        Number of stops used.
    sigma : float
        Standard error of a slope (NaN for an offset, or a slope from one stop).
    residual : float
        RMS of ``β − slope·α`` over the stops used (NaN for an offset).
    reason : str
        Why ``value`` is NaN; empty when it is not.
    source_tracking_id : str
        ``tracking_id`` of the caldip cast.
    extra : dict
        Further facts for the provenance attributes (e.g. the conductivity range of the
        stops a slope was fitted over).

    """

    variable: str
    kind: str
    value: float
    rule: str
    stop_pressures: tuple[float, ...] = ()
    n: int = 0
    sigma: float = float("nan")
    residual: float = float("nan")
    reason: str = ""
    source_tracking_id: str = ""
    extra: dict = field(default_factory=dict, compare=False)

    @property
    def available(self) -> bool:
        """True when the dip gave a value."""
        return bool(np.isfinite(self.value))


def usable_stops(record: xr.Dataset, variable: str) -> np.ndarray:
    """Return a mask of the stops usable for one variable.

    A stop is usable when its difference and CTD pressure are finite and its caldip flag says
    the comparison exists: ``ok`` — and, in a schema-1 cast, also ``flagged``, which there
    only means the instrument reads off by more than caldip's acceptance tolerance (what a
    correction is for). ``no_data``, ``missing`` and ``unknown`` stops are not used. Whether
    the water at a stop was stable enough is decided separately (:func:`stable_stops`).

    Parameters
    ----------
    record : xarray.Dataset
        One instrument's record, indexed over ``stop``.
    variable : str
        ``"temperature"``, ``"conductivity"`` or ``"pressure"``.

    Returns
    -------
    numpy.ndarray of bool
        One entry per stop.

    """
    stem = STEMS[variable]
    flag = np.asarray(record[f"{stem}_flag"].values)
    diff = np.asarray(record[f"{stem}_diff"].values, dtype=float)
    press = np.asarray(record["ctd_press"].values, dtype=float)
    accepted = CALDIP_FLAGS_USABLE if _schema(record) == 1 else (CALDIP_FLAG_OK,)
    return np.isin(flag, accepted) & np.isfinite(diff) & np.isfinite(press)


def _schema(record: xr.Dataset) -> int:
    """Return the cast's caldip schema version (1 when it cannot be read)."""
    try:
        return int(record.attrs.get("schema_version", 1))
    except (TypeError, ValueError):
        return 1


def stable_stops(record: xr.Dataset, variable: str) -> tuple[np.ndarray, str]:
    """Return the usable stops where the CTD saw stable water, and the rule in words.

    A stop counts when the CTD's standard deviation there (``ctd_<var>_std``) is at most
    :data:`STABLE_CTD_STD` for the variable. Pressure has no stability test. A cast without
    ``ctd_<var>_std`` (caldip schema 1) cannot be tested: every usable stop counts, and the
    rule says so.

    Parameters
    ----------
    record : xarray.Dataset
        One instrument's record.
    variable : str
        ``"temperature"``, ``"conductivity"`` or ``"pressure"``.

    Returns
    -------
    tuple of (numpy.ndarray of bool, str)
        The stop mask and the stability rule applied (empty for pressure).

    """
    ok = usable_stops(record, variable)
    if variable not in STABLE_CTD_STD:
        return ok, ""
    name = f"ctd_{STEMS[variable]}_std"
    if name not in record:
        return ok, "stability not checked (no CTD std in the cast)"
    limit = STABLE_CTD_STD[variable]
    std = np.asarray(record[name].values, dtype=float)
    return ok & (std <= limit), f"CTD std ≤ {limit:g}"


def through_origin_slope(
    alpha: np.ndarray, beta: np.ndarray, weights: np.ndarray | None = None
) -> tuple[float, float, float]:
    """Return the Sea-Bird AN31 slope ``β ≈ s·α``, its standard error and the RMS residual.

    ``s = Σ w·α·β / Σ w·α²``; with ``r = β − s·α``,
    ``σ_s = √(Σ w·r² / (n − 1) / Σ w·α²)`` (NaN for one point).

    Parameters
    ----------
    alpha : numpy.ndarray
        Instrument values (AN31's α).
    beta : numpy.ndarray
        Reference values (AN31's β).
    weights : numpy.ndarray or None, optional
        Per-point weights; equal weights when ``None``.

    Returns
    -------
    tuple of float
        ``(slope, sigma, rms_residual)``.

    Examples
    --------
    The bottle example in Sea-Bird Application Note 31:

    >>> s, _, _ = through_origin_slope(
    ...     np.array([4.63421, 3.25349, 3.16777]), np.array([4.63481, 3.25398, 3.16822])
    ... )
    >>> round(s, 6)
    1.000138

    """
    alpha = np.asarray(alpha, dtype=float)
    beta = np.asarray(beta, dtype=float)
    w = np.ones_like(alpha) if weights is None else np.asarray(weights, dtype=float)
    saa = float(np.sum(w * alpha * alpha))
    slope = float(np.sum(w * alpha * beta)) / saa
    r = beta - slope * alpha
    n = alpha.size
    sigma = float(np.sqrt(np.sum(w * r * r) / (n - 1) / saa)) if n > 1 else float("nan")
    return slope, sigma, float(np.sqrt(np.mean(r * r)))


def _unavailable(
    variable: str, kind: str, rule: str, reason: str, tid: str
) -> Reduction:
    """Return a Reduction that says why the dip gives no value."""
    return Reduction(
        variable, kind, float("nan"), rule, reason=reason, source_tracking_id=tid
    )


def _no_deep_reason(record: xr.Dataset) -> str:
    """Say why no conductivity stop qualified: none usable, none stable, or none deep enough."""
    if not usable_stops(record, "conductivity").any():
        return "no usable stop"
    stable, rule = stable_stops(record, "conductivity")
    word = "usable" if rule.startswith("stability not checked") else "stable"
    if not stable.any():
        return f"no {word} stop"
    return f"no {word} stop below {SHALLOWEST_STOP_DBAR:g} dbar"


def _tid(record: xr.Dataset) -> str:
    """Return the cast's tracking_id."""
    return str(record.attrs.get("tracking_id", ""))


def deepest_offset(record: xr.Dataset, variable: str) -> Reduction:
    """Return the offset at the deepest stable stop (``standard`` temperature).

    Parameters
    ----------
    record : xarray.Dataset
        One instrument's record.
    variable : str
        The variable; ``"temperature"`` under ``standard``.

    Returns
    -------
    Reduction
        The offset, or NaN with ``reason``.

    """
    ok, stability = stable_stops(record, variable)
    rule = "offset at the deepest stable stop" + (
        f" ({stability})" if stability else ""
    )
    if not ok.any():
        reason = (
            "no stable stop"
            if usable_stops(record, variable).any()
            else "no usable stop"
        )
        return _unavailable(variable, "offset", rule, reason, _tid(record))
    press = np.asarray(record["ctd_press"].values, dtype=float)
    i = int(np.argmax(np.where(ok, press, -np.inf)))
    diff = float(record[f"{STEMS[variable]}_diff"].values[i])
    return Reduction(
        variable,
        "offset",
        diff,
        rule,
        stop_pressures=(float(press[i]),),
        n=1,
        source_tracking_id=_tid(record),
    )


def conductivity_stops(record: xr.Dataset) -> tuple[np.ndarray, str]:
    """Return the deep, stable conductivity stops (``standard``) and the rule that chose them.

    Of the stable stops (:func:`stable_stops`): those deeper than :data:`DEEP_STOP_DBAR` when at
    least two qualify, otherwise those deeper than :data:`SHALLOWEST_STOP_DBAR`; never
    shallower.

    Parameters
    ----------
    record : xarray.Dataset
        One instrument's record.

    Returns
    -------
    tuple of (numpy.ndarray of bool, str)
        The stop mask (all False when none qualify) and the rule, in words.

    """
    ok, stability = stable_stops(record, "conductivity")
    press = np.asarray(record["ctd_press"].values, dtype=float)
    deep = ok & (press > DEEP_STOP_DBAR)
    rule = f"stops deeper than {DEEP_STOP_DBAR:g} dbar"
    if deep.sum() < 2:
        deep = ok & (press > SHALLOWEST_STOP_DBAR)
        rule = f"stops deeper than {SHALLOWEST_STOP_DBAR:g} dbar (fewer than two below {DEEP_STOP_DBAR:g})"
    return deep, f"{rule}, {stability}"


def conductivity_slope(record: xr.Dataset) -> Reduction:
    """Return the AN31 conductivity slope over the deep, stable stops (``standard``).

    The fit is through the origin, weighted by ``N / cond_std²`` where every stop used has a
    positive ``cond_std`` (equal weights otherwise).

    Parameters
    ----------
    record : xarray.Dataset
        One instrument's record.

    Returns
    -------
    Reduction
        ``kind="slope"``; NaN with ``reason`` when no stop qualifies.

    """
    stops, rule = conductivity_stops(record)
    rule = f"AN31 slope through the origin over {rule}"
    if not stops.any():
        return _unavailable(
            "conductivity",
            "slope",
            rule,
            _no_deep_reason(record),
            _tid(record),
        )
    alpha = np.asarray(record["inst_cond"].values, dtype=float)[stops]
    beta = np.asarray(record["ctd_cond"].values, dtype=float)[stops]
    std = np.asarray(record["cond_std"].values, dtype=float)[stops]
    n_samples = np.asarray(record["N"].values, dtype=float)[stops]
    weights = None
    if np.all(np.isfinite(std)) and np.all(std > 0) and np.all(np.isfinite(n_samples)):
        weights = n_samples / std**2
        rule += ", weighted N/cond_std²"
    slope, sigma, residual = through_origin_slope(alpha, beta, weights)
    press = np.asarray(record["ctd_press"].values, dtype=float)[stops]
    return Reduction(
        "conductivity",
        "slope",
        slope,
        rule,
        stop_pressures=tuple(float(p) for p in press),
        n=int(stops.sum()),
        sigma=sigma,
        residual=residual,
        source_tracking_id=_tid(record),
        extra={"conductivity_range": (float(alpha.min()), float(alpha.max()))},
    )


def conductivity_offset(record: xr.Dataset) -> Reduction:
    """Return the mean conductivity offset over the deep, stable stops.

    The offset alternative to :func:`conductivity_slope` (a ``mean`` or ``ramp`` override
    under ``standard``), over the same stops.

    Parameters
    ----------
    record : xarray.Dataset
        One instrument's record.

    Returns
    -------
    Reduction
        The offset, or NaN with ``reason`` when no stop qualifies.

    """
    stops, rule = conductivity_stops(record)
    rule = f"mean offset over {rule}"
    if not stops.any():
        return _unavailable(
            "conductivity",
            "offset",
            rule,
            _no_deep_reason(record),
            _tid(record),
        )
    diff = np.asarray(record["cond_diff"].values, dtype=float)[stops]
    press = np.asarray(record["ctd_press"].values, dtype=float)[stops]
    return Reduction(
        "conductivity",
        "offset",
        float(np.mean(diff)),
        rule,
        stop_pressures=tuple(float(p) for p in press),
        n=int(stops.sum()),
        source_tracking_id=_tid(record),
    )


def pressure_offset(record: xr.Dataset, deployment_pressure: float) -> Reduction:
    """Return the mean pressure offset near the deployment pressure (``standard``).

    The stops within :data:`PRESSURE_WINDOW_DBAR` of ``deployment_pressure`` are averaged;
    with none, the nearest usable stop is used.

    Parameters
    ----------
    record : xarray.Dataset
        One instrument's record.
    deployment_pressure : float
        The instrument's deployment pressure (dbar).

    Returns
    -------
    Reduction
        The offset, or NaN with ``reason``.

    """
    rule = f"mean offset at the stops within {PRESSURE_WINDOW_DBAR:g} dbar of {deployment_pressure:g} dbar"
    ok = usable_stops(record, "pressure")
    if not ok.any():
        return _unavailable("pressure", "offset", rule, "no usable stop", _tid(record))
    press = np.asarray(record["ctd_press"].values, dtype=float)
    diff = np.asarray(record["press_diff"].values, dtype=float)
    near = ok & (np.abs(press - deployment_pressure) <= PRESSURE_WINDOW_DBAR)
    if not near.any():
        i = int(np.argmin(np.where(ok, np.abs(press - deployment_pressure), np.inf)))
        near = np.zeros_like(ok)
        near[i] = True
        rule = f"offset at the stop nearest {deployment_pressure:g} dbar (none within {PRESSURE_WINDOW_DBAR:g})"
    return Reduction(
        "pressure",
        "offset",
        float(np.mean(diff[near])),
        rule,
        stop_pressures=tuple(float(p) for p in press[near]),
        n=int(near.sum()),
        source_tracking_id=_tid(record),
    )


def rapid_depth_average(
    record: xr.Dataset,
    variable: str,
    interval: tuple[float, float] | None = None,
) -> Reduction:
    """Return the offset averaged over a deep pressure interval (``rapid`` T and C).

    As RAPID's ``microcat_insitu_cal.m``: the stop differences are interpolated linearly in
    CTD pressure onto a :data:`RAPID_GRID_DBAR` grid spanning ``interval``, the deepest
    stop's difference held for :data:`RAPID_EXTEND_DBAR` below it, and averaged over the grid
    points the stops cover.

    Parameters
    ----------
    record : xarray.Dataset
        One instrument's record.
    variable : str
        ``"temperature"`` or ``"conductivity"``.
    interval : tuple of float or None, optional
        ``(p_lo, p_hi)`` in dbar; default the deepest :data:`RAPID_INTERVAL_DBAR` of the
        cast's usable stops.

    Returns
    -------
    Reduction
        The offset, or NaN with ``reason``.

    """
    ok = usable_stops(record, variable)
    if not ok.any():
        return _unavailable(
            variable,
            "offset",
            "RAPID deep-interval average",
            "no usable stop",
            _tid(record),
        )
    press = np.asarray(record["ctd_press"].values, dtype=float)[ok]
    diff = np.asarray(record[f"{STEMS[variable]}_diff"].values, dtype=float)[ok]
    order = np.argsort(press)
    press, diff = press[order], diff[order]
    deepest = float(press[-1])
    if interval is None:
        interval = (deepest - RAPID_INTERVAL_DBAR, deepest)
    p_lo, p_hi = (float(x) for x in interval)
    rule = (
        f"RAPID average over {p_lo:g}–{p_hi:g} dbar on a {RAPID_GRID_DBAR:g}-dbar grid"
    )
    xp = np.append(press, deepest + RAPID_EXTEND_DBAR)
    fp = np.append(diff, diff[-1])
    grid = np.arange(p_lo, p_hi + RAPID_GRID_DBAR / 2, RAPID_GRID_DBAR)
    grid = grid[(grid >= xp[0]) & (grid <= xp[-1])]
    if grid.size == 0:
        return _unavailable(
            variable,
            "offset",
            rule,
            "the stops do not reach the interval",
            _tid(record),
        )
    used = (press >= p_lo - RAPID_GRID_DBAR) & (press <= p_hi + RAPID_EXTEND_DBAR)
    return Reduction(
        variable,
        "offset",
        float(np.mean(np.interp(grid, xp, fp))),
        rule,
        stop_pressures=tuple(float(p) for p in press[used]),
        n=int(used.sum()),
        source_tracking_id=_tid(record),
    )


def rapid_pressure_at_depth(
    record: xr.Dataset, deployment_pressure: float
) -> Reduction:
    """Return the pressure offset interpolated to the deployment pressure (``rapid``).

    As RAPID's ``microcat_insitu_cal.m``: linear in CTD pressure between stops, the deepest
    stop's offset held :data:`RAPID_EXTEND_DBAR` below it; deeper still, a polynomial in
    pressure through all usable stops (cubic when there are four or more, RAPID's
    ``dp_ext``).

    Parameters
    ----------
    record : xarray.Dataset
        One instrument's record.
    deployment_pressure : float
        The instrument's deployment pressure (dbar).

    Returns
    -------
    Reduction
        The offset, or NaN with ``reason``.

    """
    rule = f"RAPID: offset interpolated to {deployment_pressure:g} dbar"
    ok = usable_stops(record, "pressure")
    if not ok.any():
        return _unavailable("pressure", "offset", rule, "no usable stop", _tid(record))
    press = np.asarray(record["ctd_press"].values, dtype=float)[ok]
    diff = np.asarray(record["press_diff"].values, dtype=float)[ok]
    order = np.argsort(press)
    press, diff = press[order], diff[order]
    if deployment_pressure < press[0]:
        return _unavailable(
            "pressure",
            "offset",
            rule,
            "the deployment pressure is shallower than every stop",
            _tid(record),
        )
    limit = press[-1] + RAPID_EXTEND_DBAR
    if deployment_pressure <= limit:
        xp, fp = np.append(press, limit), np.append(diff, diff[-1])
        value = float(np.interp(deployment_pressure, xp, fp))
    else:
        degree = min(3, press.size - 1)
        value = float(np.polyval(np.polyfit(press, diff, degree), deployment_pressure))
        rule += f" (extrapolated, degree-{degree} fit in pressure)"
    return Reduction(
        "pressure",
        "offset",
        value,
        rule,
        stop_pressures=tuple(float(p) for p in press),
        n=int(press.size),
        source_tracking_id=_tid(record),
    )


def reduce_dip(
    record: xr.Dataset,
    variable: str,
    *,
    scheme: str = "standard",
    deployment_pressure: float | None = None,
    slope: bool = True,
) -> Reduction:
    """Return one dip's correction for one variable under a scheme.

    Parameters
    ----------
    record : xarray.Dataset
        One instrument's record.
    variable : str
        ``"temperature"``, ``"conductivity"`` or ``"pressure"``.
    scheme : str, optional
        ``"standard"`` (default) or ``"rapid"``.
    deployment_pressure : float or None, optional
        The instrument's deployment pressure (dbar); needed for pressure.
    slope : bool, optional
        Conductivity under ``standard`` only: the AN31 slope (default) or, when ``False``,
        the mean offset over the same stops (a ``mean`` / ``ramp`` override).

    Returns
    -------
    Reduction
        See the module docstring for the rule each scheme uses.

    Raises
    ------
    ValueError
        If the variable or scheme is unknown, or pressure is asked for without a
        deployment pressure.

    """
    if variable not in STEMS:
        msg = f"unknown caldip variable {variable!r}"
        raise ValueError(msg)
    if scheme not in ("standard", "rapid"):
        msg = f"unknown caldip scheme {scheme!r}"
        raise ValueError(msg)
    if variable == "pressure":
        if deployment_pressure is None:
            msg = "a pressure correction needs the deployment pressure"
            raise ValueError(msg)
        if scheme == "rapid":
            return rapid_pressure_at_depth(record, deployment_pressure)
        return pressure_offset(record, deployment_pressure)
    if scheme == "rapid":
        return rapid_depth_average(record, variable)
    if variable == "conductivity":
        return conductivity_slope(record) if slope else conductivity_offset(record)
    return deepest_offset(record, variable)
