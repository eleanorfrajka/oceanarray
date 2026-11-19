# Changelog

All notable changes to oceanarray are documented here, one line per change. Upgrading from 0.5: see
*Upgrading from 0.5 to 0.6* in the docs (`migrating_0_6_0`).

---

## [Unreleased]

### Added

- Instrument page header: "{Type} {serial} · {depth} m", nominal depth, HAB, start/end, sampling interval, records, and the originator's file (or ERDDAP dataset) linked to its archive.
- Instrument page: a Quality flags table of the originator's flags (`*_qc_originator_mapped`) beside ours, classes other than good only, and the QC test limits.
- Instrument and stack pages: a processing table with one row per `history` entry (When / What was done / Settings), settings read from the file's attributes.
- Every report page has a right-justified Expand all / Collapse all button under the jump-nav; printing opens every collapsed box and restores them afterwards.
- `spectrum_markers:` in the mooring YAML adds periods (e.g. `4 d: 4 d`) to the grid page's temperature and rotary spectra and names them in the captions; `validate` checks them.
- Grid page: σ0 and σ2 sections over the full column, from θ and practical salinity (`draw_grid_potential_density`).
- Stack and grid pages open their Processing history with a step table (Step / What was done / Settings) read from the file; the dated record is collapsed below it.
- Deployment report: a closing *Mooring description* section with the mooring YAML in a collapsed box with a Copy button, so a reader can rerun it.
- `OCEANARRAY_FIGURE_SOURCE=0` turns off the per-figure `source:` line (for a public build).
- `oceanarray concat` / `oa.concat` join one site's deployment stacks by depth into `<site>_stackcat.nc`, with a Stack cat report page (#135).
- Stage 1 writes an imported file's principal investigators and contributors as OceanSITES lists; the stack and Stack cat join them (#135).
- `writers.write(chunks=)` sets the chunk length along a dimension (#135).
- Every file names the oceanarray that wrote it (`oceanarray_version`, `+g<hash>` from a checkout; `stage<n>_oceanarray_version`) and its units changes (`units_converted`) (#135).
- `oceanarray concat` also grids the Stack cat, one mooring per profile, into `<site>_gridcat.nc` with a Grid cat report page; `--no-grid` skips it.
- `stack: mask_flags` (default 4; `--mask-flags`): what the stack pages hide and the grid leaves out, recorded as `stack_mask_flags`; `grid: mask_flags` may only add.
- Originator flags are translated to our table by meaning into `<var>_qc_originator_mapped`, with the dictionary per level; a `<CODE>_flag` is read when there is no `<CODE>_QC`.
- Flag 5 `value_changed` (oceanarray's one extension to OceanSITES 1.4): an originator flag saying its QC modified the value.
- A Quality flags section on the stack and Stack cat pages: counts per level (ours, the originator's, hidden by the rule), a flag strip, and the flag translation table.
- Each QC'd variable gets `processing_level` (OceanSITES reference table 3); interpolated pressure records its inputs' flags in `pressure_source_flags`.
- Stacks record the settings that built them (the step used, the settings blocks, the instruments) as YAML text in `deployment_settings` (#134).
- `<field>_source` in the mooring YAML (waterdepth, deployment/recovery time, latitude, longitude) is written beside the field in every file and in the Stack cat's deployment record.
- `oceanarray -q` / `-v` set the console log level (warnings only / debug); each run ends with a per-mooring warning summary, also written to `processing_logs/<name>_summary.json`.
- Stage 2 warns when a record starts well after deployment or ends well before recovery (more than 5 % of the planned window), naming the gap.
- `thermistor` is a known instrument type (OceanSITES "MT" files).
- `people:` and `source:` blocks in the mooring YAML give the people, DOI, citation, license and acknowledgement that data files do not; the file's own values win.
- Every product keeps where its data came from (`source_*`, per deployment in the Stack cat); its `citation` is its sources' own, `license` lists theirs, and how it was built is in `history` (#141).
- One OG1-style sensor record per instrument (`SENSOR_<type>_<serial>`, MicroCATs `SENSOR_CTD_…`) with each sensor's calibration; data variables name it in `sensor`.
- `run` keeps a stack and grid that are still current and says why it rebuilds; stacks and grids record their input files' SHA-256.
- An imported file's variable attributes (`comment`, `uncertainty`, `accuracy`, …) go on the sensor record as `originator_<variable>_<attribute>` (#141).
- When stage 3 recomputes salinity for an imported file, the originator's is kept as `salinity_originator`, with the difference in `history` (#141).
- Stacks name each level's originator file, its SHA-256, release dates, sampling and processing level; the originators' title, institution, project … are kept as `originator_*` (#141).
- `sensor_catalog:` in the mooring YAML names a catalog of sensor models with their NERC vocabularies; stage 1 describes each MicroCAT from it, by serial (#141).
- The mooring page lists each instrument's sensor record: model, maker, pumping, where they came from, and the originator's description (#141).
- The OceanSITES export writes `license`, `citation` and `references` (#141).

### Changed

- Instrument page: files table, history record, start/end windows and value distributions move into a collapsed Processing section; one NetCDF file appendix.
- Instrument time series and T-S mark the worse of the originator's and our flags: dots suspect, crosses bad, legend in words; captions generated from the file.
- Instrument T-S is the stack's figure: every value drawn, pressure colour bar shallow at the top, σθ labelled inside the axes; the stack T-S does the same.
- Instrument current rose is the stack's design; the direction clause comes from the velocity `comment`, else "as provided by the originator".
- Instrument value distributions keep what the flag rule keeps (ours or the originator's flag) and name the sensor resolution the originator gives.
- Start/end windows: one line where the description and suggested times agree; a disturbed-end note for originator data; reader words in the note.
- Stack page captions build the flag sentence from `stack_mask_flags` (ours and the originator's); the Stack cat captions too.
- Stack hydrography names each line at its end in the pressure panel (no legend); velocity takes the hydrography's colours, a shorter upward panel, suspect values as black dots.
- Stack roses: titled "serial · nominal depth", one ring scale for every rose, figure sized to the number of roses; grid roses share the scale too.
- Stack Instruments table: nominal depth, HAB, readable type, linked serial; spacing histogram collapsed in Processing history; one NetCDF file appendix.
- Stack clock check for originator data: a one-line "Not applicable" instead of no section; an empty figure always says why.
- Quality flags table and strip: "Instrument" (type, serial, nominal depth) for "Level", legend words for flag tokens, captions per the review.
- Stage history notes say what each stage did ("stage 2: clock corrected and trimmed ...") and the stack writes dated "stack:" and "flags:" entries.
- Salinity is labelled "Salinity (PSU)" on T-S axes, grid hydrography titles and captions (was "Practical salinity").
- Grid captions are generated from the file: interpolation, flags left out, levels chosen, window lengths, instrument count and velocity reference; jargon and wrong claims removed.
- Grid hydrography: colour limits at the 1st–99th percentiles with arrows, instrument median pressures as triangles at the right edge.
- Grid velocity sections are cropped to the velocity levels, drawn at the hydrography's dbar per inch, with triangles at the current meters.
- Grid velocity sections: the three components share one scale and colour bar; speed and direction have their own.
- Grid roses and hodographs: captions state the 50 % coverage rule and name each level (and current meter) left out for low coverage.
- Grid captions name the interpolation method only when the file records it; otherwise "interpolated in pressure".
- Caption thresholds (coverage bands, rose and spectrum coverage, target σ₀) are read from the constants that draw the figures.
- T-S figures: one σθ grid per figure over the shared axes; without a position the figure notes and logs "σθ not drawn".
- Grid page computes σ0 and σ2 once, for its Stratification sections only; other pages no longer compute them.
- Deployment QC summary counts every flag in the table, including 5 (value changed), read by its word.
- Stage 3, stack and grid compute speed, direction and σ with the report's helpers (`oceanarray.physics`); output values unchanged.
- Grid velocity profiles: drawn at the same vertical scale, down to the seabed; speed legend at centre right, labels "Middle 50 %", count axis "Hourly values".
- Stack page: "Particle trajectories" is now "Progressive vector diagram" (`draw_multi_aquadopp_progressive_vector`, `draw_adcp_progressive_vector`; old names kept as aliases).
- Stack and grid current roses share one design: speed classes 0–5/5–10/10–20/20–30/>30 cm/s, rings in %, a speed-class legend; grid roses at up to 4 levels filled ≥ 50 % of the time.
- Grid hodographs are drawn at the current roses' levels, titled by pressure.
- "Particle trajectory" on the grid page is now the progressive vector diagram (`draw_grid_progressive_vector`; `draw_grid_trajectory` kept as an alias).
- Grid temperature spectrum: 14-day and 7-day windows (right panel periods under 2 days), 3 or 5 levels at instrument pressures filled ≥ 90 %, a pressure colour bar; titles give the windows used.
- Spectra mark M2, K1 and the inertial period only by default (1.8 d, 4 d and 9 min removed from the defaults).
- Grid wavelet: at most 3 levels (outermost filled ≥ 90 % and one between), periods from 6 time steps (at least 2 h), gap-filled columns cross-hatched.
- Grid rotary spectrum: 3 levels filled ≥ 90 %, chosen by the helper the temperature spectrum and wavelet use.
- T-S σθ contours are drawn at a round interval, named in the grid caption.
- Grid NetCDF appendix: one collapsed "NetCDF file" section; serial-like, index and flag-code variables show no min/max in the variables tables.
- T-S figures plot potential temperature θ against practical salinity, with labelled σθ contours from θ at the mooring position (was in-situ T).
- Report pages derive θ, speed, direction and the knockdown inputs once (`oceanarray.analysis.derived`) and share them across their figures.
- `pipeline.pressure.pressure_from_depth` / `depth_from_pressure`: one depth↔pressure conversion, used by ADCP QC and the figures.
- Deployment report: the instrument summary comes first; processing pipeline, deployment timing and clock corrections follow it, the pipeline table collapsed.
- Deployment report tables identify instruments by nominal depth and HAB (units on their own header line) with readable type names; sampling interval as "1 h", pressure range as "2358–2376".
- Deployment report: originator timing, clock corrections and calibration show one line when there is nothing to show.
- Deployment report QC summary: a row only for variables not all good; columns and legend from the flags present.
- QC summary: variables never checked (flag 0), nominal (7) or interpolated (8) throughout get one line per flag, never counted as good.
- Stack, grid, Stack cat and Grid cat pages link the deployment page's *Mooring description* on the expand-all line, left-justified with the button at the right (one link per deployment).
- Instrument summary caption states why a row is shaded (record ends more than 12 h before recovery).
- Report captions rewritten for readers who did not run the pipeline (deployment report); flag words replace the table tokens ("suspect", "interpolated", "no QC").
- Report footer: the package name links to the documentation; cruise and config path removed.
- Deployments on the Stack cat page are colored red to purple in time order; their start lines are dotted.
- Site figures: up to seven deployments get a color each; more share colors in pairs, the second dashed, hatched or with a square marker.
- A ▼ above each Stack cat and Grid cat time axis marks a deployment's start.
- The Stack cat T-S diagram shows each level's 50 % outline and median, joined per deployment, instead of every point.
- The Stack cat has current direction by depth beside speed; both are drawn at a depth only when at least half of that depth's own samples in the deployment have valid velocity (so a depth sampled for only part of a deployment is judged on its own record).
- The Grid cat T-S diagram marks each T-and-S instrument at its mean pressure.
- Grid cat isopycnals are labeled with the value only; display bins with data for less than half their time are blank; sections have no horizontal grid.
- Site time means are summed in float64.
- Minimum matplotlib raised to 3.10 (the by-deployment speed panel uses `boxplot(orientation=)`, added in 3.10).
- The Stack cat's depth columns are named for what they are: dimension `N_DEPTHS`, `depth_index`, `nominal_depth`, `depth_family`, `depth_match_value`, `depth_tolerance`, `overlap_depth_index`; the YAML keys are `stack: depth_match` / `depth_tolerance_m`.
- Numbers in global attributes (`dp_dbar`, `p_start_dbar`, `dt_seconds` ...) are stored as `float32` / `int32` like the variables; positions stay double.
- Report pages list principal-investigator and contributor email, url and id under People & institutions.
- Pipeline progress goes to stderr via standard logging (one line per step; `-v` for per-instrument detail); a command's result still goes to stdout.
- The Stack cat's Instruments table names the instrument type per row and gives each deployment one column (serial and depth) (#141).
- The stack page leaves out the clock alignment check when every instrument was imported from an originator's processed files (#141).

### Deprecated

- `log_fn=` on `to_canonical_units`, `interpolate_pressure`, the ADCP-QC functions and the other transforms — configure logging instead (removed in 0.7.0).
- `oceanarray.logger.enable_logging` / `disable_logging` — set `logging.getLogger("oceanarray").disabled` or use `logging.disable` (removed in 0.7.0).

### Breaking changes

- The stack keeps every value with ours and the originator's flags; suspect (3) values are shown and gridded by default (`stack: mask_flags: [3, 4]` keeps the old way).
- In the stack and Stack cat a value with no flag variable is 0 (not QC'd) and a missing one 9; flags are int8.
- Interpolated pressure never uses an input flagged 4 or 9; such times are missing (9), not interpolated.
- Raw Sea-Bird stage files hold one `SENSOR_<type>_<serial>` record instead of `SENSOR_TEMP_` / `_CNDC_` / `_PRES_<serial>`.
- Grids change: velocity flagged 3 (suspect) is kept unless `mask_flags` says otherwise (the default leaves out 4), and current speed and direction come from the gridded east and north velocity.

### Fixed

- The deployment page's QC table no longer repeats the Good column (once from Good %, once from the per-flag columns).
- Rose pages no longer warn of a missing declination when the originator rotated to true north (stack `originator_magnetic_declination`); the warning names who and why.
- Thermistors, TR-1050 and Seapoint instruments get readable type names on report pages (were upper-cased codes).
- The deployment page's regrid command names the mooring description and `--from 3`, so it runs as copied (no raw files needed).

- A relative `sensor_catalog` in a mooring YAML reached through a symbolic link is read from the real file's folder, not the link's.
- `append_history` cleans "; " inside a note to ", ", and the salinity, flag-translation, QC-count, people and source notes no longer contain it, so `history` reads back whole.
- Velocity-profile seabed line and the Overflow near-bottom level convert the water depth to pressure (they read metres as dbar).
- σθ contours on T-S figures were computed from in-situ temperature at 0° N 0° E; now from θ at the mooring position.
- N²: an unknown longitude warns instead of silently using 0.
- Knockdown figures compare the design depth converted to pressure (`gsw.p_from_z`) with measured pressure; they compared metres with dbar, a 1–1.5 % bias read as knockdown.
- Knockdown figures: an unknown latitude warns and leaves out the design pressure instead of using the equator; the HAB axis stops at the water depth.
- `OCEANARRAY_FIGURE_SOURCE` is read when a page renders, not when oceanarray is imported.
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
