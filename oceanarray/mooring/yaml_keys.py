"""What a mooring YAML may hold: its top-level keys and the shape of some values."""

#: The mooring-level fields whose origin or correction a mooring YAML may record
#: as ``<field>_source`` (free text), carried into every file beside the field.
SOURCE_FIELDS: tuple[str, ...] = (
    "waterdepth",
    "deployment_time",
    "recovery_time",
    "latitude",
    "longitude",
)

# Keys the fixtures, ``cmd_init`` and real UHH configs are known to use. The
# list is deliberately generous: ``cmd_init`` writes ``deployment_cruise`` /
# ``recovery_ship`` etc. while older configs use ``cruise`` / ``ship`` /
# ``status``, and both are valid. An unknown top-level key is a WARNING (not an
# ERROR) for now so a real config carrying a key the fixtures never showed is
# not rejected; promotion to ERROR is planned for a later release, once this list
# has been checked against the configs on the drive (``scripts/survey_mooring_keys.py``).
KNOWN_KEYS: frozenset[str] = frozenset(
    {
        # where a mooring-level value came from, or why it was corrected
        *(f"{field}_source" for field in SOURCE_FIELDS),
        # identity / header
        "name",
        "cleaname",
        "year",
        "waterdepth",
        "status",
        # OceanSITES identity (read by the export; see ``_check_export_codes``)
        "platform_code",
        "deployment_code",
        # cruise / ship (both vocabularies)
        "cruise",
        "ship",
        "deployment_cruise",
        "deployment_ship",
        "recovery_cruise",
        "recovery_ship",
        # time
        "deployment_time",
        "recovery_time",
        # position: the bare pair and the three graded tiers the position chain
        # reads in order of preference (seabed → deployment → planned → latitude).
        "latitude",
        "longitude",
        "seabed_latitude",
        "seabed_longitude",
        "deployment_latitude",
        "deployment_longitude",
        "planned_latitude",
        "planned_longitude",
        # QC thresholds (read by the QC pass; shapes checked by _check_qc_overrides).
        "qc_ranges",
        "qc_spike",
        "tilt_qc",
        "qc_flat_line",
        # calibration-dip correction (read at the front of stage 3; ``_check_caldip``)
        "caldip",
        # Deprecated: a raw-data root in the mooring YAML is a run-time fact; use
        # raw_dir / --raw-dir. check() WARNs; the override is removed in 0.7.0.
        "directory",
        # instrument lists
        "clamp",
        "instruments",
        "inline",
        # optional stack / grid settings for oceanarray.run (``_check_settings``)
        "stack",
        "grid",
        # reference pressure (dbar) of the stack's potential density
        "density_reference",
        # optional report sections to turn on, keyed by page (``_check_report_sections``)
        "report_sections",
        # extra periods marked on the grid page's spectra (``_check_spectrum_markers``)
        "spectrum_markers",
        # people and data source for files that name none (``_check_people_and_source``)
        "people",
        "source",
        # what each instrument is, by serial: the sensor record's model and vocabularies (``catalog``)
        "sensor_catalog",
    }
)

#: A deployment code: ``YYYYMM`` with a calendar month (01–12).
DEPLOYMENT_CODE_PATTERN = r"\d{4}(0[1-9]|1[0-2])"
