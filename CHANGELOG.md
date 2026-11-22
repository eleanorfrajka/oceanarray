# Changelog

All notable changes to oceanarray are documented here, one line per change. Upgrading: see
*Upgrading from 0.6.0 to 0.6.1* (`migrating_0_6_1`) and *Upgrading from 0.5 to 0.6* (`migrating_0_6_0`) in the docs.

---

## [Unreleased]

### Added

- Developer guide (`developer`): principles, layers, one idiom per job, vocabulary, naming, branches and pull requests, known deviations; replaces the style guide (#154).
- Configuration files page (`configuration_files`): the YAML files, which value wins, finding a mooring by name or by path; a sensor catalog page (`sensor_catalog`) with an example (#154).

### Changed

- The mask-rule helpers (`resolve_mask_flags`, `stack_rule`, ...) moved to `oceanarray.mask_rules` and the originator flag vocabulary to `oceanarray.originator_flags`, unchanged (#163).
- Problems with the user's input raise `OceanarrayError` subclasses: the new `ReaderError` from the readers, `MooringError` for YAML content; both still subclass `ValueError` (#161).
- Data problems found while reading or reporting are logged as WARNINGs instead of Python `UserWarning`s, so they reach the log files; report progress lines go through logging too (#160).
- An unreadable file or header is skipped with a WARNING naming the file, no longer silently (#159).
- A missing processing folder or mooring YAML raises `MooringError`; `MooringReport.generate` and `generate_recovery_table` returned `None` (#159).
- The clock tools moved from `oceanarray.pipeline.clock` to `oceanarray.analysis.clock`, unchanged; the old path warns until 0.8.0 (#158).
- Import rules are checked in CI by `lint-imports` (contracts in `pyproject.toml`), docstring examples run as doctests, and `AGENTS.md` points coding assistants to the developer guide (#156).
- Every figure and the instrument summary hide flagged values by the `mask_flags` rule (default 4), like the grid: suspect (3) values are now drawn on instrument pages and on stacks without a recorded rule, and the Stack cat page marks them with black dots as the stack page does. Re-run the report to apply (#155).
- Mooring check: warns when a recovery clock correction is over 120 s from the mooring median (`CLOCK_DRIFT_OUTLIER_SECONDS`), and errors on a non-numeric `clock_offset`/`clock_drift_seconds` (#157).

### Fixed

- `oceanarray stamp` on a missing file reports it instead of creating an empty file (#161).
- Deployment and array pages: an instrument's pressure range leaves out what the mooring's `stack: mask_flags` hides, like its figures (it used the default rule, so a mooring hiding suspect values showed suspect pressures in its range) (#156).
- Clock: a recovery reading of `unknown` (or empty) is a warning, not an error; the instrument's drift is not corrected and its stage-2 history says so (#157).
- Deployment page clock table: the recovery correction shows the sign stage 2 applies (computer − instrument); it showed the opposite. Not-read instruments are listed (#157).
- Deployment page clock table: the caption says a correction was applied only when at least one was (#157).
- Stage-2 history records "clock corrected" only when a correction was made, and otherwise why not (readings agree, not read, or originator time) (#157).
- Stage files carry a `clock_offset` variable only when a non-zero offset was applied; stage 1 no longer writes a zero. Rerun stage 2 to update existing files (#157).
- Mooring YAML template: the `clock_offset` comment described the drift (#157).

### Removed

- `config.parameters.QC_BAD_FLAGS` (figures hide flags by the `mask_flags` rule: `pipeline.masking.masked` / `flagged`) and the unused `percent_good_qc_var` argument of `draw_adcp_progressive_vector`.
- Unused helpers and constants: `plotters.diagnostic.draw_knockdown_pressure` (not drawn on any page), `plotters.flags.worst_flags`, `paths.product_glob`, `mooring.REQUIRED_KEYS`, `readers.rodb.RODB_EXTENSIONS` and `config.parameters` `INSTRUMENT_COLORS`, `FIGURE_SIZE_TALL`, `SIGMA_CONTOUR_LEVELS`.

### Breaking changes

What to do for each: *Upgrading from 0.6.1 to 0.7.0* (`migrating_0_7_0`).

- Grid files name their levels `pmin_dbar` / `pmax_dbar` (were `p_start_dbar` / `p_end_dbar`, still read); the grid defaults are `config.parameters.GRID_*_DBAR`, not `_run.DEFAULTS` (#162).
- All package errors derive from the new `OceanarrayError`; `StageError` is no longer a `MooringError` and moves to `oceanarray.exceptions` (#161).
- `qc_ranges: pitch` / `roll` are no longer applied and `pitch_qc` / `roll_qc` are no longer written or stacked: tilt QC is geometric only (`tilt_qc`); the mooring check warns if they are set.

---

## [0.6.2] — 2026-11-21

### Changed

- Tests: the `needs_seasenselib` and `integration` markers are removed (seasenselib is a core dependency); CI installs the built wheel and opens its data files.

### Fixed

- Stack: an ADCP bin's `instrument_depth` is its own (the head's depth ∓ range), not the head's; bins at or above the surface are left out. Restack to apply.
- Stack: a current meter imported with east/north velocity only gets `current_speed` and `current_direction` (named in their `comment`).
- Stack and stage pages: tilt lines at the stored QC limits, else `QC_TILT` (30°/50°, not 20°/30°); the scatter panel is blank without pitch or roll.
- Deployment page: the Aquadopp speed profile leaves out a level with no speed; the rose grid takes at most four bins per ADCP.
- Stack cat page: an ADCP is a median line and middle-50 % band per deployment; roses and flags show three bins per ADCP; bins above the surface go.
- Stack cat page: every flag bar has one height; ADCP bins left out are named as one range per deployment.
- Flag strip: bars half as tall; an ADCP is one panel at its head (its pressure once, per-bin variables a row per bin shown); collapsed on the Stack cat page.
- `validate` knows `density_reference`.

---

## [0.6.1] — 2026-11-20

### Added

- ADCP import: `file_type: oceansites` reads originator ADCP profiles (`TIME × BINDEPTH`) as one ADCP on `(time, N_BINS)`, with their bin pressures; "MT" files as `thermistor`.
- ADCP import: `file_type: rodb` reads per-bin `.edt` files from one `filename: <name>_bin*.edt` pattern as one ADCP; other file types refuse a wildcard matching several files.
- `oceanarray concat` / `oa.concat` join a site's stacks by depth and grid them (`<site>_stackcat.nc`, `<site>_gridcat.nc`), each with a report page (#135).
- The Grid cat page rotates velocity to the record-mean flow, or to `concat --rotate-to DEG` / `generate_gridcat_page(rotate_to=)`.
- Year page for an array: moorings by `subarray:`, deployed and recovered dates with cruises and ships, instruments by function; `--bathymetry FILE` adds GEBCO or ETOPO contours.
- Report pages: a Processing history table from `history`, Expand all / Collapse all, a link to the *Mooring description* (YAML); `OCEANARRAY_FIGURE_SOURCE=0` drops figure `source:` lines.
- Grid page: σ0 and σ2 sections over the full column; `spectrum_markers:` in the mooring YAML adds periods to the spectra.
- `stack: mask_flags` (default 4; `--mask-flags`): what the stack pages hide and the grid leaves out, recorded as `stack_mask_flags`.
- Originator flags are translated by meaning into `<var>_qc_originator_mapped`, shown beside ours on the report pages; flag 5 `value_changed` marks a value their QC modified.
- Each QC'd variable gets `processing_level` (OceanSITES reference table 3); interpolated pressure records its inputs' flags in `pressure_source_flags`.
- Every file names the oceanarray that wrote it (`oceanarray_version`) and its units changes (`units_converted`); `writers.write(chunks=)` sets chunk lengths (#135).
- Products keep their sources (`source_*`, originator files with SHA-256, `originator_*`); `citation` and `license` are the sources' own, also in the OceanSITES export (#141).
- Stacks record the settings that built them in `deployment_settings` (#134); `run` keeps a stack and grid that are still current and says why it rebuilds.
- Mooring YAML: `people:` and `source:` blocks fill in what data files lack; `<field>_source` is written beside waterdepth, times and position (#135).
- One OG1-style sensor record per instrument (`SENSOR_<type>_<serial>`) with its calibration; `sensor_catalog:` adds models and NERC vocabularies by serial (#141).
- An imported file's variable attributes go on its sensor record (`originator_<variable>_<attribute>`); a recomputed salinity keeps the originator's as `salinity_originator` (#141).
- Stage 2 warns when a record starts well after deployment or ends well before recovery (more than 5 % of the planned window).

### Changed

- OceanSITES positions (`BINDEPTH`, `DEPTH`, `PRES`, `INSTRDEPTH`) outside the file's valid range read as missing, counted in `history`.
- Output attributes: velocity, speed and direction from the variable registry (key `current_speed`); units `degree`; global-attribute numbers `float32` / `int32`.
- `history` has one dated entry per step, saying what was done, in every stage, the stack, `concat` and `grid`.
- Logging: progress goes to stderr (one line per step), `oceanarray -q` / `-v` set the level, and each run ends with a warning summary (`processing_logs/<name>_summary.json`).
- Deployment page: instrument summary first, instruments by nominal depth and HAB, QC summary only for variables not all good, captions in words.
- Instrument page: a header (type, serial, depths, times, sampling, originator's file linked); figures mark the worse of both flag sets; processing details collapsed.
- Stack page: lines named at their ends, roses on one ring scale, captions built from the file; no clock check when every instrument was imported (#141).
- Grid page: captions built from the file; sections share scales and mark instrument pressures; roses, hodographs and spectra pick levels by coverage.
- T-S figures plot potential temperature θ against practical salinity, labelled "Salinity (PSU)", with σθ contours at a round interval.
- Spectra mark M2, K1 and the inertial period only by default (1.8 d, 4 d and 9 min removed); add others with `spectrum_markers:`.
- "Particle trajectories" are progressive vector diagrams (`draw_*_progressive_vector`; old `*_trajectories` / `draw_grid_trajectory` names kept as aliases).
- Minimum matplotlib raised to 3.10.

### Deprecated

- `log_fn=` on `to_canonical_units`, `interpolate_pressure`, the ADCP-QC functions and the other transforms — configure logging instead (removed in 0.7.0).
- `oceanarray.logger.enable_logging` / `disable_logging` — set `logging.getLogger("oceanarray").disabled` or use `logging.disable` (removed in 0.7.0).

### Breaking changes

What to do for each: *Upgrading from 0.6.0 to 0.6.1* (`migrating_0_6_1`).

- An ADCP without `orientation: up|down` is refused (`validate` error, stage 1); there is no `down` default.
- `InstrDepth` is now `instrument_depth` (m, from `nominal_depth`, else water depth − HAB; the YAML `depth` key is not read).
- `serial_number` is now `instrument_serial`, always text; the stack's `serial` stays the per-level key. Older stage files are read under the new names.
- Positions (`latitude`, `longitude`, `waterdepth` …) are numbers, not text, and left out when absent; `deployment_time` / `recovery_time` are ISO 8601 UTC with `Z`.
- `valid_min` / `valid_max` are written only where a physical limit is known, no longer from the gross-range limits.
- Tilt QC flags velocity on the geometric tilt at `QC_TILT` 30° / 50°, even where `pitch_qc` / `roll_qc` exist; their gross-range flags no longer reach velocity.
- Raw Sea-Bird stage files hold one `SENSOR_<type>_<serial>` record, not `SENSOR_TEMP_` / `_CNDC_` / `_PRES_<serial>`; no stage file carries `sample_interval_seconds`.
- Stacks write σ0 as `sea_water_sigma_theta`, σ1…σ4 with no standard name, long names `σ₀` … `σ₄`; magnetic-north velocity and direction carry no standard name.
- The stack keeps every value with both flag sets; suspect (3) is shown and gridded by default (`mask_flags: [3, 4]` keeps the old way); no flag variable reads 0, missing 9 (int8).
- Interpolated pressure never uses an input flagged 4 or 9; such times are missing (9).
- Grids keep velocity flagged 3 unless `mask_flags` says otherwise; current speed and direction come from the gridded east and north velocity.
- Site grids no longer record `slope_angle_deg` (use `--rotate-to`); `site.slope_components` is `site.rotated_components`, `SLOPE_STEADINESS_MIN` is `ROTATION_STEADINESS_MIN`.
- The year page ignores the array YAML's cruise, ship, dates, positions, water depths, `vocab` and `bathymetry` (one warning names them); `line:` is now `subarray:`.

### Fixed

- OceanSITES reader: a variable without a `standard_name` gets the registry's, not an empty one; one on a second dimension is refused by name, not a bare `ValueError`.
- `oceanarray plot` and `process --plot` no longer leave the report style set for later figures in the same Python session.
- Rose pages no longer warn of a missing declination when the originator rotated to true north.
- Knockdown figures, the velocity-profile seabed line and the Overflow near-bottom level convert depth to pressure (they read metres as dbar); they and N² warn without a position.
- σθ contours on T-S figures are computed from θ at the mooring position (were in-situ temperature at 0° N 0° E).
- A grid's current direction is no longer interpolated across 0/360° (it pointed the wrong way between a 355° and a 5° instrument).
- `stage2_source_sha256` is the full SHA-256, not its first 8 digits (#135).
- The stack time axis keeps a last step that falls exactly on the recovery time (#135).

---

## [0.6.0] — 2026-11-15

### Added

- `import oceanarray as oa` gives the verbs: `oa.mooring`, `read`, `read_mooring`, `stage1`–`stage3`, `process`, `stack`, `grid`, `run`, `write`, `write_mooring`, `report`, `export`, loaded on first use (#114, #115, #117, #118).
- `oceanarray.mooring`: one loader for the mooring description (`load`, `check`, `template`, `save`) and `oa.MooringError` (#114).
- Pure stages over datasets: `stage1`, `stage2`, `stage3`, `stack`, `grid`, with `read_mooring` / `write_mooring` for the files (#115, #117).
- `oceanarray.readers`: one package reading a raw file by its content, not its suffix (#115).
- `processing_stage` marker on every stage file, and `oceanarray stamp` to mark files written before it (#117, #123).
- `oceanarray.process` runs stages 1–3; `oceanarray.run` adds the stack, grid and (with `report=True`) the report (#118).
- `keep_going` (on by default): a failed instrument is logged and left out, and the products record it (`instruments_omitted`, `keep_going_failures`, `source_stage`) (#118).
- Optional `stack:` (`dt_seconds`) and `grid:` (`pmin`, `pmax`, `dp`) blocks in the mooring description (#118).
- `oceanarray.report` / `report_paths`; the report finds the mooring's folder wherever its description is (#118).
- The CLI takes a mooring as its `*.mooring.yaml`, its folder, or a name with `--proc-dir`; `--only` selects instruments (#118).
- `data_mode = "P"` on every stage-3, stack and grid file (#118).
- An interpolated pressure records its source files (name, SHA-256, time); stage 3 warns when they have changed (#118).
- OceanSITES 1.4 export: `oceanarray export` / `oa.export` write deployment files and gridded (`GRD`) files (#122).
- `platform_code` and `deployment_code` in the mooring description, for the OceanSITES file names (#122).
- Calibration dips, read and reduced but not yet applied: `read_caldip_cast`, the `caldip:` block, `caldip_ends`, `reduce_dip` (#125, #126, #127).
- Pressure in `kPa` and the OOI ERDDAP unit spellings; `units_override` can carry a `reason` (#129).
- `report_sections` in the mooring YAML turns on optional report sections, e.g. `{grid: [overflow]}` (#130).
- Reports of moorings built from imported OceanSITES / RODB files: no failed panels, a "Current meter" label, a timing table that fits (#130).
- `n_records_without_time`: every reader drops records with no time and counts them (#128, #131).
- `location.parse_coordinate` (#132).
- A migration page, *Upgrading from 0.5 to 0.6* (#118).

### Changed

- Stack: default step 3600 s (was 60 s); each instrument takes its nearest sample within ±dt/2, no time interpolation, on a time axis of whole steps; to keep 60 s, set `stack: {dt_seconds: 60}` (#133).
- Report mastheads (mooring, stack, grid, recovery table) come from one fact list: four cruise/ship fields, positions in degrees and decimal minutes, durations in months over 6 months, instruments counted by type (#132).
- An instrument's `hab` is filled from `hab_bottom` / `hab_top` by its `orientation`, for clamp and inline entries alike (#131).
- `oceanarray process` carries on past a failed instrument and exits 0; `--strict` stops at the first failure (#118).
- A usage error exits 2 with an `ERROR:` line; a processing failure exits 1 (#123).
- `oceanarray validate` takes a mooring like the other commands and gains `--strict` (#123).
- Every command's help says what it reads and writes, with examples; renamed flags are accepted with a note until 0.7.0 (#118, #123).
- The CLI is a package, `oceanarray.cli`, one module per command; `python -m oceanarray` runs it (#118, #123).
- Required description keys are `name` and `waterdepth`; a missing deployment window is a warning (#114).
- Output files are named from `name:`, not the folder name (#115, #117).
- Stacking reads each instrument's sampling interval from its time stamps, not `sample_interval_seconds` (#117).
- Stage 3 reads the whole mooring's stage-2 files for pressure, whichever instruments are asked for (#118).
- Stage 2 raises `MooringError` on a `deployment_time` / `recovery_time` it cannot parse (was: treated as absent) (#114).
- `oceanarray report` exits 1 when the mooring report fails (was: ignored) (#118).
- In-memory datasets hold the values their files will hold (float32, time encoding), so a full run and a re-run agree (#118).
- Private helpers follow one naming table (#119).

### Deprecated

Each old name warns and is removed in 0.7.0; the list is `oceanarray._deprecated.RENAMED` and the migration page.

- `oceanarray.parameters` → `oceanarray.config.parameters` (#118).
- The processor classes (`MooringProcessor`, `Stage2Processor`, `Stage3Processor`, `MooringStacker`, `MooringGridder`) → `process` / `run` or the pure stages (#118).
- `oceanarray.process(name, stage, proc_dir=…)` → `oceanarray.process(yaml_path, …)` (#118).
- `process --stage stack|grid` → `oceanarray run` (#118).
- `inspect.vars` / `attrs` → `inspect.variables` / `attributes` (#118).
- `legacy.process_rodb.normalize_by_middle_percent` / `normalize_dataset_by_middle_percent` → `to_zscore` / `to_zscore_dataset` (#121).
- CLI flags: `--serial` → `--only`, `--dt` → `--dt-seconds`, `process --report` → `--summary`, `plot --downsample` → `--downsample-seconds`, `export --overwrite` → `--force`, `stamp --stage` → `--declare`, `report -i/-s/-g/-A` → the long forms (#118, #123).
- `config.validation.validate_mooring_yaml` → `oceanarray.mooring.check` (#114).
- `plotters.timeseries.draw_grid` → xarray's `.plot.pcolormesh` (#121).
- Stage files without a `processing_stage` marker are read with a warning; mark them with `oceanarray stamp` (#117).
- `directory:` in a mooring description → `raw_dir` / `--raw-dir` (#114).

### Breaking changes

- Stack and grid files change: the hourly default, nearest sample and whole-step axis (#133), and a clamp ADCP given only `hab_bottom` / `hab_top` now stacked (#131).
- A QC override block with an unknown field, a malformed value, or a new variable without a `fail_span` is refused by `load` and an ERROR in `validate` (#123).
- An instrument whose `orientation` lacks its hab key (`hab_top` up, `hab_bottom` down) is refused by `load` and an ERROR in `validate` (#131).
- `oceanarray.processors` is now `oceanarray.pipeline`, with no shim (#115).
- `oceanarray.tools`, `oceanarray.utilities` and `pipeline.helpers` are gone; old → new on the migration page (#115, #120, #121).
- Figure-returning plot functions are `draw_<what>`, with no alias (#118).
- `writers.write` refuses to replace a file unless `overwrite=True`, and returns the path (#115).
- `TimeGriddingProcessor` and its filters are removed (#118).
- Removed as 0.5 announced: `canonicalise_units`, `format_units`, `UNIT_ALIASES`, `VARIABLE_UNIT_ALIASES` (#118).
- `oceanarray run` exits 0 when it finishes with instruments left out; `--strict` exits 1 (#118).
- The grid report's Overflow section is off unless `report_sections` asks for it (#130).
- `current_direction` uses `standard_name = "sea_water_velocity_to_direction"` (#123).

### Fixed

- A Nortek header with an unreadable coordinate system is `"UNK"` and stage 3 refuses to rotate it (was silently ENU) (#116).
- Timeless records (`NaT`) no longer crash stage 2; a RODB fill in `HH` is no longer read as midnight (#128, #131).
- Moorings with no position survive stage 3, the stack and the report (declination `"UNK"`) (#131).
- Report plots leave out only flags 3, 4 and 9, so interpolated pressure (8) is drawn (#118).
- Positions never read `60.00′`; a `nan` position reads `—` (#132).
- The report rebuilds a stale stack, grid or instrument page even when the summary is fresh (#118).
- Report text keeps its size on phones (#118).
- `bool` attributes are written as `int8` (#117).

## [0.5.1] — 2026-10-23

### Changed

- Report layer vendored from ctdcast into `reports/_vendored/`; NetCDF tables, history tables and the array map come from it (#106, #111).
- Units come from the shared tables (`config.unit_tables`); plot labels from `unit_label` (#107).
- ADCP bin pressures are tilt-corrected, except where the firmware already placed the bins (#112).
- `°` is accepted as `degree` (#110); N² keeps the spelling it arrives with (direct commit).
- Legacy vocabulary files moved under `legacy/` and are no longer shipped (#107).
- The CLI reference page is generated from the parser (#105).

### Added

- Readers for originator files: `file_type: oceansites` and `rodb`, and the `currentmeter` instrument (#108).
- `units_override` per instrument (#108).
- `stack: false` per instrument keeps it out of the stack and grid (#108).
- Unit spellings `decibar`, `meter/second`, `meter`, `psu`, `cm s-1` (#108).

### Deprecated

- `canonicalise_units`, `format_units`, `UNIT_ALIASES`, `VARIABLE_UNIT_ALIASES` (removed in 0.6.0) (#107).

## [0.5.0] — 2026-10-12

### Added

- `validate` refuses duplicate, blank and reserved serials (#101).

### Changed

- One NetCDF inventory appendix on the instrument, stack and grid reports (#103).
- Masthead nav from the shared macro (#100).
- One netCDF writer, `oceanarray.writers.write`; stage files shrink 58–66% (#98).
- Stage logs are written as UTF-8 (#98).
- cftime time coordinates keep their units and calendar (#99).
- Python 3.13 supported (#99).

### Breaking changes

- One units spelling per quantity in every file; an unknown or missing `units` stops that instrument (#102).
- `oceanarray process` runs stage 3 by default (#102).
- Oxygen saturation and AOU recomputed on a per-mass basis (were ~2.5% low) (#102).
- Python 3.11 or newer (#99).
- `tools.writers.save_dataset` and `save_OS_instrument` removed (#99).
- `None` attributes are dropped, not written as `""` (#99).
- Time is written as `seconds since 1970-01-01`; raw stored values change (#99).

## [0.4.0] — 2026-09-08

### Added

- caldip stub: the contract and a null `caldip_dir` hook in stage 3 (#92).
- `report --pdf` and `--pdf-dir` (#69, #90).
- Manifest-driven report pages (#78, #79, #80).
- `run` takes `-o`, `--report-dir`, `--sig-level` (#88).
- Zenodo DOI (#68); a hosted dune2 example report (#82).

### Changed

- Report subsystem refactor: vendored tokens and CSS, golden pages, `report/` → `reports/` (#70–#77, #83, #84).
- Packaging in `pyproject.toml`, CI matrix, ruff gate (#81, #85, #86).

### Fixed

- Cross-page links resolve in the merged PDF (#89).
- QC-flag resampling (#87).
- `--help` no longer crashes on Python 3.11 (#88).

### Breaking changes

- `oceanarray stack` / `grid` and `oceanarray logsheet` removed (#88).
- Python 3.10 or newer (#85).

## [0.3.0] — 2026-08-11

### Breaking changes

- `oceanarray.logsheet` removed; use the standalone `logsheet` package (#66).
- `oceanarray run` skips the grid when the stack fails (#65).

### Fixed

- Grid N² uses the mooring's latitude (was 0°) (#67).

### Changed

- One `STAGES` registry drives the CLI (#65); plot and report hardening, `VARIABLES` registry (#64); golden stage-1 tests (#63).

## [0.2.0] — 2026-08-07

### Breaking changes

- `oceanarray logsheet` disabled; `--basedir` removed (use `--raw-dir`, `--proc-dir`) (#52).
- `plotter.py` retired; import from `oceanarray.plotters.*` (#53).
- Processing modules moved to `oceanarray.processors.*` (#49, #59).

### Added

- `process --stage stack grid`, and `oceanarray.process()` (#58).
- Array report (#48); rotary spectra and wave diagnostics for ADCPs (#47).
- Release automation on `v*` tags (#61).

### Fixed

- QC flags follow the OceanSITES table (#54); irregular time axes in report figures (#50); placeholder turbidity units (#61).

### Deprecated

- `oceanarray stack` / `grid` (removed in 0.4.0) (#62); `rbr-hex-oa` and `sbe-asc` remapped (#60).

## [0.1.0] — 2026-07-27

First public release: stages 1–3 (Sea-Bird, Nortek, RBR, RDI), stack and grid, HTML reports (mooring, instrument, stack, grid, array), and the `process`, `run`, `validate`, `list` and `logsheet` commands.
