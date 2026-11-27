"""The sensor catalog: what each instrument is, by serial, for its sensor record.

A mooring YAML names one catalog (``sensor_catalog: <path>``, relative to it).
``models:`` gives per model the attributes of the OG1 sensor record
(``SENSOR_<type>_<serial>``) and their NERC vocabularies, with one ``sensor_model`` for
the instrument with a pressure sensor and one without (L22 lists them apart);
``instruments:`` maps the maker's label (``"37-12046"``) to a model and its report
sources::

    models:
      SBE 37-SMP:
        sensor_type: CTD
        sensor_type_vocabulary: https://vocab.nerc.ac.uk/collection/L05/current/130/
        sensor_maker: Sea-Bird Scientific
        sensor_maker_vocabulary: https://vocab.nerc.ac.uk/collection/L35/current/MAN0013/
        with_pressure:
          sensor_model: Sea-Bird SBE 37 MicroCat SMP-CTP (submersible) CTD sensor
          sensor_model_vocabulary: https://vocab.nerc.ac.uk/collection/L22/current/TOOL1457/
        without_pressure:
          sensor_model: Sea-Bird SBE 37 MicroCat SMP-CT with optional pressure ...
          sensor_model_vocabulary: https://vocab.nerc.ac.uk/collection/L22/current/TOOL0018/
    instruments:
      "37-12046":
        model: SBE 37-SMP
        pumped: true
        sources:
          - {report: 2015_64PE400_NIOZ_cruise_report.pdf, pdf_page: 40,
             text: "Instrument - MicroCat SBE 37-SMP SN 12046 88m --> 172 m"}

No calibrations are kept here (they come from the instrument's files). Stage 1 uses
the catalog when the instrument's entry names no ``sensor_model``
(:func:`oceanarray.pipeline.sensors.build_record`).
"""

from __future__ import annotations

import functools
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import numpy as np
import xarray as xr
import yaml

from oceanarray import originator_flags
from oceanarray.exceptions import MooringError

#: The maker's label prefix of each instrument type, as the catalog keys it.
PREFIX: dict[str, str] = {"microcat": "37-"}

#: The mooring YAML key that names the catalog file.
KEY = "sensor_catalog"

#: Record attributes a model gives (the pressure choice adds ``sensor_model`` and
#: ``sensor_model_vocabulary``).
MODEL_ATTRS: tuple[str, ...] = (
    "long_name",
    "sensor_type",
    "sensor_type_vocabulary",
    "sensor_maker",
    "sensor_maker_vocabulary",
)

#: The two ways a model is listed, by whether the instrument measured pressure.
PRESSURE_KEYS: tuple[str, str] = ("with_pressure", "without_pressure")

#: Our flags that say a pressure value was measured and good enough to keep.
_GOOD = (1, 2)


def key_for(instrument: str, serial: object) -> str | None:
    """Return the catalog key of an instrument, or None for a type it does not cover.

    Parameters
    ----------
    instrument : str
        The entry's ``instrument`` (e.g. ``"microcat"``).
    serial : object
        The serial (``12046``, ``"12046"``).

    Returns
    -------
    str or None
        ``"37-12046"``, or None when *instrument* has no prefix in :data:`PREFIX`.
    """
    prefix = PREFIX.get(str(instrument or "").lower())
    return f"{prefix}{str(serial).strip()}" if prefix else None


def _problems(data: object) -> list[str]:
    """Return what is wrong with a parsed catalog (an empty list when it is usable)."""
    if not isinstance(data, dict) or not isinstance(data.get("instruments"), dict):
        return ["no 'instruments:' mapping at the top"]
    models = data.get("models") or {}
    if not isinstance(models, dict):
        return ["'models:' is not a mapping"]
    out = []
    for name, model in models.items():
        for key in PRESSURE_KEYS:
            entry = model.get(key) if isinstance(model, dict) else None
            if not isinstance(entry, dict) or not entry.get("sensor_model"):
                out.append(f"model {name!r}: no {key}: sensor_model")
    for key, entry in data["instruments"].items():
        if not isinstance(entry, dict):
            out.append(f"{key}: not a mapping (model, pumped, sources)")
            continue
        if entry.get("pumped") is not None and not isinstance(entry["pumped"], bool):
            out.append(f"{key}: pumped {entry['pumped']!r} is not true or false")
        model = entry.get("model")
        if model is not None and model not in models:
            out.append(f"{key}: model {model!r} is not under 'models:'")
    return out


@functools.lru_cache(maxsize=8)
def _load(path: str, mtime: float) -> dict[str, Any]:  # noqa: ARG001 — mtime keys the cache
    """Read and check a catalog file (cached per path and modification time)."""
    try:
        data = yaml.safe_load(Path(path).read_text())
    except (OSError, yaml.YAMLError) as e:
        raise MooringError(f"sensor_catalog {path}: cannot be read: {e}") from e
    if problems := _problems(data):
        raise MooringError(f"sensor_catalog {path}: " + "; ".join(problems))
    return data


def load(path: str | Path) -> dict[str, Any]:
    """Return a catalog: ``{"models": {...}, "instruments": {...}}``.

    Parameters
    ----------
    path : str or pathlib.Path
        The catalog file.

    Returns
    -------
    dict
        The parsed file, checked: every model lists ``with_pressure`` and
        ``without_pressure``, and every instrument's ``model`` is a listed model.

    Raises
    ------
    MooringError
        When the file cannot be read or fails the checks.
    """
    path = Path(path)
    if not path.is_file():
        raise MooringError(f"sensor_catalog {path}: no such file")
    return _load(str(path.resolve()), path.stat().st_mtime)


def _serials(entry: Mapping[str, Any], file_serial: object) -> list[str]:
    """Return the serials to look up: the entry's, then the file's (``12046.0`` → ``12046``)."""
    out = []
    for value in (entry.get("serial"), file_serial):
        text = str(value or "").strip()
        if text.endswith(".0") and text[:-2].isdigit():
            text = text[:-2]
        if text and text not in out:
            out.append(text)
    return out


def lookup(
    mooring: Mapping[str, Any],
    entry: Mapping[str, Any],
    file_serial: object = None,
) -> dict[str, Any]:
    """Return what the mooring's catalog says of one instrument, or ``{}``.

    The instrument is looked up by its entry's serial, then by the serial its
    file states (an imported file's ``sensor_serial_number``), since a
    mooring YAML may name an imported instrument by a label such as ``MC76``.

    Parameters
    ----------
    mooring : Mapping
        The mooring YAML; its ``sensor_catalog`` is a path (made absolute
        by :func:`oceanarray.mooring.load`).
    entry : Mapping
        The instrument's entry (``instrument``, ``serial``).
    file_serial : object, optional
        The serial the instrument's file states.

    Returns
    -------
    dict
        The instrument's catalog entry, with ``catalog_key``, ``catalog_file`` and,
        when it names a model, that model's block under ``model_attrs``; ``{}``
        when the mooring names no catalog or the catalog lacks the instrument.
    """
    path = mooring.get(KEY)
    if not path:
        return {}
    data = load(path)
    for serial in _serials(entry, file_serial):
        key = key_for(entry.get("instrument", ""), serial)
        if key is not None and key in data["instruments"]:
            found = dict(data["instruments"][key])
            found.update(catalog_key=key, catalog_file=Path(path).name)
            if found.get("model") is not None:
                found["model_attrs"] = data["models"][found["model"]]
            return found
    return {}


def measured_pressure(ds: xr.Dataset) -> tuple[bool, str]:
    """Return whether an instrument measured pressure, and why that was decided.

    It did when its file holds pressure values and, where the originator flagged
    them, at least one is flagged good (1) or probably good (2), or none was
    quality-controlled (all 0): pressure flagged only interpolated (8), bad or
    missing was not measured by this instrument.

    Parameters
    ----------
    ds : xarray.Dataset
        The instrument's dataset at stage 1 (``pressure``, and
        ``pressure_qc_originator`` for an imported file).

    Returns
    -------
    measured : bool
        Whether to describe the instrument by its "with pressure" model entry.
    why : str
        One phrase for the log.
    """
    if "pressure" not in ds.data_vars:
        return False, "no pressure values"
    here = np.isfinite(ds["pressure"].values)
    if not here.any():
        return False, "no pressure values"
    if "pressure_qc_originator" not in ds.data_vars:
        return True, "pressure values, not flagged"
    lut = {
        num: ours
        for num, _, ours in originator_flags.dictionary(ds["pressure_qc_originator"])
        if ours is not None
    }
    # Only the samples with a value: a gap flagged 9 says nothing of the sensor.
    raw = np.asarray(ds["pressure_qc_originator"].values)[here]
    ours = np.vectorize(lambda n: lut.get(int(n), 0), otypes=[np.int8])(raw)
    if np.isin(ours, _GOOD).any():
        return True, "pressure flagged good"
    if (ours == 0).all():
        # Not quality-controlled is not the same as not measured.
        return True, "pressure values, not quality-controlled (flag 0)"
    seen = ", ".join(f"{f}: {int((ours == f).sum())}" for f in np.unique(ours).tolist())
    return False, f"pressure values, but none flagged good (flags {seen})"


def record_attrs(found: Mapping[str, Any], measured: bool) -> dict[str, str]:
    """Return the record attributes a catalog entry gives.

    Parameters
    ----------
    found : Mapping
        From :func:`lookup`.
    measured : bool
        From :func:`measured_pressure`: choose the model's ``with_pressure`` or
        ``without_pressure`` entry.

    Returns
    -------
    dict of str to str
        The model's attributes (:data:`MODEL_ATTRS`), ``sensor_model`` and
        ``sensor_model_vocabulary``, ``long_name`` (the model's, else
        ``sensor_model``), ``sensor_pumped`` when stated and
        ``sensor_model_source`` (the entry's ``sources``); ``{}`` for an entry
        with no model and no pumping.
    """
    out: dict[str, str] = {}
    model = found.get("model_attrs") or {}
    if model:
        out.update({k: str(model[k]) for k in MODEL_ATTRS if model.get(k)})
        chosen = model[PRESSURE_KEYS[0] if measured else PRESSURE_KEYS[1]]
        out["sensor_model"] = str(chosen["sensor_model"])
        if chosen.get("sensor_model_vocabulary"):
            out["sensor_model_vocabulary"] = str(chosen["sensor_model_vocabulary"])
        out.setdefault("long_name", out["sensor_model"])
        out["sensor_model_source"] = sources_text(found)
    if isinstance(found.get("pumped"), bool):
        out["sensor_pumped"] = str(found["pumped"]).lower()
    return out


def digest(mooring: Mapping[str, Any], records: list[Mapping[str, Any]]) -> str:
    """Return the SHA-256 of what the mooring's catalog says of these instruments.

    Only the entries that match the instruments' sensor records (by
    ``sensor_serial_number`` or ``originator_sensor_serial_number``), with their
    models, count: an edit to another instrument's entry, or a comment, changes
    nothing here.

    Parameters
    ----------
    mooring : Mapping
        The mooring YAML (``sensor_catalog``).
    records : list of Mapping
        The attributes of each sensor record (``SENSOR_*``) of a stack.

    Returns
    -------
    str
        The hex digest, or ``""`` when the mooring names no catalog.
    """
    import hashlib
    import json

    path = mooring.get(KEY)
    if not path:
        return ""
    try:
        data = load(path)
    except MooringError:
        return "unreadable"
    found = {}
    for attrs in records:
        for serial in _serials(
            {"serial": attrs.get("sensor_serial_number")},
            attrs.get("originator_sensor_serial_number"),
        ):
            for prefix in PREFIX.values():
                if (key := f"{prefix}{serial}") in data["instruments"]:
                    entry = data["instruments"][key]
                    found[key] = {
                        **entry,
                        "model_attrs": data["models"].get(entry.get("model"), {}),
                    }
    text = json.dumps(found, sort_keys=True, default=str)
    return hashlib.sha256(text.encode()).hexdigest()


def sources_text(found: Mapping[str, Any]) -> str:
    """Return a catalog entry's ``sources`` as one line.

    Parameters
    ----------
    found : Mapping
        A catalog entry (from :func:`lookup`); its ``sources`` is a list of
        ``{report, pdf_page, text}`` mappings or of strings.

    Returns
    -------
    str
        ``"report.pdf p.46: text; ..."``, or ``""`` when there are none.
    """
    parts = []
    for src in found.get("sources") or []:
        if isinstance(src, Mapping):
            page = f" p.{src['pdf_page']}" if src.get("pdf_page") is not None else ""
            text = f": {src['text']}" if src.get("text") else ""
            parts.append(f"{src.get('report', '')}{page}{text}".strip())
        elif src:
            parts.append(str(src))
    return "; ".join(parts)
