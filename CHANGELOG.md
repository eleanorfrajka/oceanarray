# Changelog

All notable changes to oceanarray are documented here, one line per change. Upgrading from 0.5: see
*Upgrading from 0.5 to 0.6* in the docs (`migrating_0_6_0`).

---

## [Unreleased]

### Added

- `oceanarray concat` / `oa.concat` join one site's deployment stacks by depth into `<site>_stackcat.nc`, with a Stack cat report page (#135).
- Stage 1 writes an imported file's principal investigators and contributors as OceanSITES lists; the stack and Stack cat join them (#135).
- `writers.write(chunks=)` sets the chunk length along a dimension (#135).
- Every file names the oceanarray that wrote it (`oceanarray_version`, `oceanarray_commit`, `stage<n>_oceanarray_version`); units changes are recorded in `units_converted` (#135).
- `oceanarray concat` also grids the Stack cat, one mooring per profile, into `<site>_gridcat.nc` with a Grid cat report page; `--no-grid` skips it.
- `mask_flags` (API, `grid: mask_flags` in the YAML, `--mask-flags` on `run` and `concat`): the QC flags left out before gridding, recorded as `grid_mask_flags`.
- `<field>_source` in the mooring YAML (waterdepth, deployment/recovery time, latitude, longitude) is written beside the field in every file and in the Stack cat's deployment record.

### Changed

- Deployments on the Stack cat page are colored red to purple in time order; their start lines are dotted.
- The Stack cat's depth columns are named for what they are: dimension `N_DEPTHS`, `depth_index`, `nominal_depth`, `depth_family`, `depth_match_value`, `depth_tolerance`, `overlap_depth_index`; the YAML keys are `stack: depth_match` / `depth_tolerance_m`.

### Breaking changes

- Grids change: velocity flagged 3 (suspect) is kept unless `mask_flags` says otherwise (the default leaves out 4 and 9), and current speed and direction come from the gridded east and north velocity.

### Fixed

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
- Stacks record the settings that built them (the step used, the settings blocks, the instruments) as YAML text in `deployment_settings` (#134).
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
