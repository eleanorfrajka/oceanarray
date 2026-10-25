# Changelog

All notable changes to oceanarray are documented here.

---

## [Unreleased]

### Added

- `oceanarray.pipeline.read_mooring.read_mooring(mooring, *, stage=None, serials=None, allow_missing=False, mooring_dir=None, raw_dir=None)` — reads every instrument of one mooring into a dict of datasets keyed by serial. A mooring's identity is its `name:` field (every file is `<name>_<serial>_stage<N>.nc`); `mooring` may be a dict, a `*.mooring.yaml` path, or a directory holding one. `stage=None` finds and reads each raw file (from `raw_dir`) as stage 1 does — a missing `filename` found by the recovery-file naming convention, the Nortek header (also in a legacy `nortek/` folder), the raw header's calibration metadata — and returns it unmarked; entries marked `skip` are left out; `stage=1|2|3` opens the stage files (from `mooring_dir`) and checks the marker; on every path the description's facts are stamped (per-instrument: serial, instrument, hab, clamp_id, sample_interval_seconds, file_type, source_file; per-mooring: mooring_name, latitude/longitude + source, deployment/recovery window + source, waterdepth, density_reference). A dataset whose `serial` contradicts the description, or whose `mooring_name` differs from `name:` by more than case, raises `MooringError`; a case-only difference is corrected with a `history` line. Position and window keep their placeholders when absent, loudly (`location_source`/`deployment_window_source` = `"none"`, a WARNING). A listed instrument with no file at the requested stage raises unless `allow_missing=True`.
- `oceanarray.pipeline.write_mooring.write_mooring(datasets, mooring_dir, *, overwrite=False)` — writes a dict of per-instrument datasets to `<mooring_dir>/<instrument>/<name>_<serial>_stage<N>.nc`, taking the name, instrument, serial and stage from each dataset's attributes; refuses a dataset whose `serial` attribute does not match its key.
- `oceanarray.paths.stage_file(mooring_dir, name, instrument, serial, stage, *, tag="")` and `oceanarray.paths.product_file(mooring_dir, name, product)` (`product` in `paths.PRODUCTS` = `{"stack", "grid"}`) — the full-path builders for a mooring's stage files and products, keyed on the mooring `name`; `read_mooring`/`write_mooring` use them. With them, `paths.stage_suffix`, `stage_glob` and `product_glob` give the filename endings and search patterns; no other module spells `_stageN`, `_stack.nc` or `_grid.nc` (a test enforces it).
- `oceanarray.readers` — a package reading one raw instrument file to a canonical dataset. `readers.read(path, *, file_type=None)` sniffs the file content (never the suffix) to pick the format, finds the Nortek `.hdr` beside a data file, and dispatches to a per-format module (`sbe`, `nortek`, `rodb`, `oceansites`, `netcdf`). A netCDF is read as OceanSITES when it carries a `TIME` dimension and an OceanSITES parameter code, and returned unchanged otherwise; an unrecognised file with no `file_type=` raises. The per-format reader bodies moved here unchanged from `processors.stage1` and the former `tools.readers`.
- `oceanarray.pipeline._stage` — the `processing_stage` marker: `stamp(ds, stage, *, source=)` writes the marker, a `history` line and `source_file`; `require(datasets, stage, next_stage)` raises `StageError` when a dataset is at the wrong stage, naming the stage to run first. A file written before the marker existed is tolerated for one release (a missing marker on read is a WARNING).
- `oceanarray.mooring` — the single seam for reading a mooring description: `load` (path, name + `proc_dir`, or dict; validates and raises `MooringError` on an invalid config), `check` (never raises; adds an unknown-top-level-key warning), `template` and `save`, plus `oceanarray.MooringError`. Every stage, report and CLI path now receives the loaded dict instead of calling `yaml.safe_load` itself; a test (`test_config_sites`) fails if a new mooring-config `safe_load` appears outside the loader. `scripts/survey_mooring_keys.py` lists the top-level keys on a processed-data root that are not yet in `mooring.KNOWN_KEYS`.
- `oceanarray.pipeline.stage1.stage1(datasets, mooring)`, `oceanarray.pipeline.stage2.stage2(datasets, mooring)`, `oceanarray.pipeline.stage3.stage3(datasets, mooring, *, caldip_dir=None)`, `oceanarray.pipeline.stack.stack(datasets, mooring, *, dt_seconds=60)` and `oceanarray.pipeline.grid.grid(ds_stack, *, p_start, p_end, dp)` — the processing steps as pure functions over datasets, with no file I/O. `MooringProcessor`, `Stage2Processor`, `Stage3Processor`, `MooringStacker` and `MooringGridder` now read, call these, and write; their outputs are unchanged in value. `oceanarray.pipeline.raw_files` holds stage 1's raw-file finding (`resolve_raw_file`, `guess_instrument_filename`) and reading (`read_raw_file`).
- `oceanarray stamp FILE... --stage N` and `oceanarray.pipeline.stamp.stamp_file(path, stage)` — write the `processing_stage` marker into a stage file written before it existed (`history`: "declared via stamp"). Refuses a stage that contradicts the filename or an existing marker; leaves an already-marked file untouched.
- `oceanarray.process(mooring, *, stages=(1, 2, 3), serials=None, force=False, mooring_dir=None, raw_dir=None, caldip_dir=None) -> list[Path]` — runs stages 1–3 for one mooring from its `*.mooring.yaml` path (or a directory holding one, or a loaded description with `mooring_dir=`), writing each stage's files and a log per stage, and returns every stage file of the stages run — written, or kept because it was already there (`force=True` rewrites; stage 1 also rewrites a file older than its raw file); each stage prints how many it wrote and kept. A failure raises `MooringError` naming the instruments that failed, after the stage has written the rest; the later stages do not run. Stacking and gridding are not part of it. The previous form, `process(name, stage, proc_dir=…)` (all five stages, returns a bool), still works for one release (see *Deprecated*).
- A stage-3 instrument whose pressure is interpolated records the stage-2 files it came from: `pressure_source_serials` and `pressure_source_files` (file name, SHA-256, modification time) on its `pressure` variable. When stage 3 keeps or skips such a file and its sources have changed since, a WARNING names it.
- `utilities.instrument_entries(mooring)` — the one place the `clamp`/legacy-`instruments` fallback and the inline merge live, used by every stage, report and the CLI; it treats a present-but-null `clamp:`/`inline:` as empty (previously a `TypeError`).

### Changed

- Stages 1, 2 and 3 return their datasets holding the values their files will hold — float64 stored as float32 (time and position excepted), time rounded to the file's float64-seconds encoding — so a dataset passed on in memory equals the same file read back, and a full run and a later single-stage re-run agree exactly. Written files are unchanged. `oceanarray.writers.netcdf.as_stored(ds)` does the conversion.
- Stage 3 always reads the whole mooring's stage-2 files and writes only the instruments asked for (`serials=` / `--serials`). Before, a run for one sensorless instrument read only its own file, found no pressure source, and wrote it without pressure.
- Required mooring-config keys are now `name` and `waterdepth` only; `deployment_time` and `recovery_time` are recommended — absent, they produce a WARNING (Stage 2 can detect the window from the records) rather than the ERROR `validate` used to report.
- Stage 2 raises `MooringError` on a present-but-unparseable `deployment_time`/`recovery_time` (a log-sheet typo) instead of silently treating it as absent; a genuinely absent value is still tolerated (`NaT`).
- `stack` raises `MooringError` naming both keys when the config has no deployment window (the common time axis is that window) instead of a bare `KeyError`.
- Stage 1 names its output files from the description's `name:`, as stages 2 and 3 do (previously from the mooring directory name).
- Stage 1, 2 and 3 files carry `processing_stage` and the description's stamped facts (instrument, serial, hab, position, deployment window, …).
- Stacking detects each instrument's sampling interval from its time stamps; `sample_interval_seconds` in the mooring description is no longer read.
- `oceanarray report --dry-run` previews a half-filled config without raising: it prints the validation issues and lists what parses, and only stops on a file that is not YAML at all.
- `oceanarray init` builds its stub through `mooring.template` + `mooring.save`; the written file is byte-identical to before.
- `mooring.KNOWN_KEYS` (the vocabulary the unknown-key warning checks against) gains the position tiers `seabed_*` / `deployment_*` / `planned_*`, `qc_ranges` and `directory`, confirmed against real mooring descriptions.

### Deprecated

- `oceanarray.process(name, stage, proc_dir=…)` — warns (`DeprecationWarning`) and is removed in 0.7.0. Pass the `*.mooring.yaml`: `oceanarray.process(yaml_path, raw_dir=…)` runs stages 1–3, `oceanarray.run(yaml_path, …)` runs stages 1–3, stack and grid.
- `config.validation.validate_mooring_yaml(path)` — use `oceanarray.mooring.check`, which reads the file once and adds the unknown-key check. The wrapper warns and will be removed in a future release.
- Stage files without a `processing_stage` marker (written before 0.6) — read with a WARNING and their stage inferred from the filename; refused from 0.7.0. Mark them once with `oceanarray stamp`.
- `directory:` in a mooring description — a raw-data root belongs to `raw_dir` / `--raw-dir`, not the YAML; `check` warns, and the stage-1 absolute-path override is removed in 0.7.0.

### Breaking changes

- `TimeGriddingProcessor`, `time_gridding_mooring` and `process_multiple_moorings_time_gridding` are removed from `oceanarray.pipeline.grid`, with their optional low-pass/de-tiding/band-pass filters. No notebook or CLI verb used them; the stack (`oceanarray.pipeline.stack.stack`) is the one way to put a mooring on a common time axis.
- `oceanarray.processors` is renamed to `oceanarray.pipeline` (the verbs' bodies). Old → new: every `oceanarray.processors.*` import becomes `oceanarray.pipeline.*` (`stage1`, `stage2`, `stage3`, `stack`, `grid`, `qc`, `coordinate`, `pressure`, `units`, `caldip`, `helpers`, `process`, `run`). No deprecation shim.
- `oceanarray.writers.write(ds, path)` now refuses to replace an existing file by default — pass `overwrite=True` to replace one (the processing stages do). It logs an INFO line (`overwriting <path>`) when it replaces a file, and returns the `Path` written instead of `None`. Previously it overwrote silently.
- Output filenames are built from the mooring's `name:` field, not its directory name. A directory whose basename differs from `name:` is a WARNING (previously framed as "name must match the directory"); the files are named from `name`.
- `oceanarray.tools.readers` is removed; its readers moved to the `oceanarray.readers` package. Old → new: `oceanarray.tools.readers.read_oceansites` → `oceanarray.readers.read_oceansites` (or `oceanarray.readers.oceansites.read_oceansites`); `read_rodb` → `oceanarray.readers.read_rodb`; `load_nortek_csv` → `oceanarray.readers.load_nortek_csv`. The dead `load_dataset` and `rodbload_old` are removed with no replacement — use `oceanarray.readers.read` and `read_rodb`.

### Fixed

- The netCDF writer stores `bool` attributes as `int8` (0/1) instead of failing: netCDF has no boolean attribute type.
- A Nortek header whose coordinate system cannot be read is now marked `coordinate_system = "UNK"` instead of being assumed `"ENU"`, and stage 3 refuses to rotate `UNK` velocities (with a WARNING) rather than passing them through as if geographic. Previously a BEAM/XYZ instrument with an unrecognised header was silently labelled ENU, skipping the BEAM→ENU transform and magnetic-declination correction — a silent physical error in the velocity output.

## [0.5.1] — 2026-10-23

> The units-display entries — *Units tables come from the shared report layer*, *N² keeps the spelling it arrives with*, *Legacy vocabulary files moved and renamed*, and the two *Deprecated* items — landed as direct-to-main commits (`cdcffc9`/`8982831`/`735adaa`, the units display sweep), not a pull request; [#107](https://github.com/ocean-uhh/oceanarray/pull/107) documented the sweep and added the derived-quantity guard. See `.claude/done/ROADMAP.md`.

### Changed

- **Post-release tidy** ([#105](https://github.com/ocean-uhh/oceanarray/pull/105)): `find_best_dtype` passes `timedelta64` variables through with their native kind instead of miscasting them; the dead `proc_machine` report-context key is removed; the CLI reference page is generated from the argument parser (`sphinx-argparse`); CONTRIBUTING and the README scope line refreshed.

- **Vendored report-design layer adopted** ([#106](https://github.com/ocean-uhh/oceanarray/pull/106)): the report presentation layer moves to `oceanarray/reports/_vendored/`, a directory copied byte-identical from ctdcast `15fa38b` that carries its own `MANIFEST.txt` and self-check (`check.py`). The six scattered vendored files (`config/report_tokens.py`, `config/report.mplstyle`, `reports/_encode.py`, `_css.py`, `_manifest.py`, `templates/_nav.html`), the root `VENDORED.txt`, and `scripts/check_vendored.py` are removed; a new owned `reports/design.py` holds the oceanarray-specific configuration (package accent, QC/sensor predicates, `ATTR_GROUP_EXTRAS`, the per-figure source toggle). `reports/__init__.py` resolves its public names lazily so importing the vendored layer does not pull the page pipeline. No change to the NetCDF outputs or the public API.
- **NetCDF-inventory tables rendered by the vendored builders** ([#106](https://github.com/ocean-uhh/oceanarray/pull/106)): the coordinates/variables/scalars/globals tables now come from the shared `nc_tables` builders and `_nc_tables.html` macros. Type labels read `string` rather than `str`, each table carries a `source:` caption, the coordinates table gains a Dims column, and the `history` global attribute is shown once (in its dedicated processing-history panel, excluded from the globals table via the builder's `exclude=`). An all-empty Standard/long-name column drops along with units/min-max/attributes (`optional=` includes `stdname`). The QC-companion marker shows the file's real suffix — `(_qc)` for oceanarray's lowercase QC variables, not a hard-coded `(_QC)` — and is coloured with the shared `--qc-mark` token. The local inventory templates and row builders are removed.
- **Processing-history panel is now tables** ([#106](https://github.com/ocean-uhh/oceanarray/pull/106)): the instrument/grid/stack reports render the processing history as a key/value `properties_table` for the header items plus a sortable Time·Event table for the dated entries (a `|`-joined entry splits into same-time rows), replacing the `dl`-style list that wrapped long entries into orphan rows. The Time·Event table carries a leading `#` column — the row's 1-based position in the `history` attribute — so sorting any column never loses the original sequence (events sharing one timestamp would otherwise be unorderable).
- **Array page map** ([#106](https://github.com/ocean-uhh/oceanarray/pull/106)): the mooring-position map is the vendored Leaflet `track_map` (a marker per mooring, each carrying a permanent name label in the marker's colour, hover-linked to the mooring list via `data-track-id`, offline bundled assets) behind the `design.ARRAY_MAP_LEAFLET` flag — the interactive map on screen, its matplotlib PNG fallback when printing (never both). The static PNG gains a `source:` caption, a Mercator-square framed extent (a line of moorings no longer renders as a sliver), and family marker colours; the mooring name links to its report and the jump-nav uses the family markup.
- **Array masthead metadata** ([#106](https://github.com/ocean-uhh/oceanarray/pull/106)): the array summary masthead gains deployment/recovery and duration, position and mooring extent, water-depth range, an instrument by-type breakdown, deployment status, and processed-to counts — all read or derived from the array/mooring YAMLs at render (a missing value is an em-dash).
- **base.html de-duplicated against the shared stylesheet** ([#106](https://github.com/ocean-uhh/oceanarray/pull/106)): report chrome rules that `emit_css()` already provides are removed from the per-page `<style>` block (only the oceanarray-specific additions remain), guarded by `test_no_base_rule_duplicates_vendored_css`.
- **Absent figure panels say why** ([#106](https://github.com/ocean-uhh/oceanarray/pull/106)): a figure whose draw raises now renders a short stub with the reason instead of vanishing (`as_panel`, ported from ctdcast).
- **Coverage floor raised** ([#106](https://github.com/ocean-uhh/oceanarray/pull/106)) from 38% to 58%: `main` and this branch both measure 59% unit coverage, so the CI `--cov-fail-under` gate is ratcheted to one point below.
- **Units tables come from the shared report layer**: the vendored layer is re-copied from ctdcast `f2af1d8`, where `units_display.py` became `units.py` + `units_tables.py` (canonical spellings, accepted aliases with factors, display labels, keyed by CF `standard_name`). `oceanarray.config.unit_tables` holds oceanarray's differences from those tables (N² stored as `s-2`; the `ml l-1`, `mL/L` and `s⁻²` spellings) and `unit_label()`, the display entry point the plotters now call. The storage pass is `processors.units.to_canonical_units`. It accepts the same spellings as before for every registry quantity (N² aside, below), still takes each variable's canonical spelling from `parameters.VARIABLES`, still raises on a missing units attribute, and still records `units_converted_from` / `units_conversion_factor` on a conversion; the dune2 goldens are byte-identical.
- **Plot-axis units derived from the registry through the pipeline** ([#107](https://github.com/ocean-uhh/oceanarray/pull/107)): `parameters.vunit`/`vlabel` now format each variable's canonical CF `units` through `unit_label` (`degree_Celsius` → `°C`), so every axis/colorbar label is produced by the one display rule; the duplicate `label_units` column is removed from `VARIABLES`. The attitude/engineering variables `pitch`, `roll`, `heading`, `tilt`, `current_direction`, `bin_pressure` are registered (canonical CF `degree`/`dbar`), and the reader spellings `degrees`/`volts` are accepted as aliases of `degree`/`V`; the storage pass rewrites a stored `degrees` to `degree` at factor 1. Rendered labels are unchanged.
- **Derived-quantity unit guard** ([#107](https://github.com/ocean-uhh/oceanarray/pull/107)): a plot whose output unit is computed from a source variable (displacement in km from velocity, height-above-seabed in m from pressure, instrument spacing in dbar, temperature PSD) warns via `unit_tables.warn_if_not_canonical` when the source variable does not carry the canonical unit the derivation assumes. Silent on every oceanarray-written file (the storage pass guarantees canonical units); it catches a foreign or hand-edited file the derivation would otherwise mislabel.
- **N² keeps the spelling it arrives with**: `s-2` and `rad2 s-2` are both accepted and neither is rewritten into the other (`rad2 s-2` used to be refused), so N² is the one quantity whose stored spelling can differ between files.
- **Legacy vocabulary files moved and renamed**: `config/OS1_var_names.yaml`, `config/OS1_sensor_attrs.yaml` and `legacy/OS1_vocab_attrs.yaml` are now `legacy/OS1_variable_names.yaml`, `legacy/OS1_sensor_attributes.yaml` and `legacy/OS1_variable_attributes.yaml`, beside the legacy `convertOS.py` that takes them as inputs. No other oceanarray code reads them; the wheel no longer ships them.
- **Report figure layout** ([#110](https://github.com/ocean-uhh/oceanarray/pull/110)): a single-tier current rose (one QC tier, no magnetic panel) renders as a third-slot circle centred in whitespace instead of a full-slot-width square; the knockdown-displacement figure reserves left margin for its pressure y-labels so a deep mooring's four-digit dbar labels no longer clip. The unreachable `GRID_COMBINED_HYDRO` grid profile is removed (T-S stays its own section).
- **`°` accepted as a degree spelling** ([#110](https://github.com/ocean-uhh/oceanarray/pull/110)): the storage pass accepts the `°` symbol as the canonical `degree` (factor 1) for attitude variables. `deg` is not accepted here — it is scoped to latitude/longitude in the vendored unit tables and arrives, scoped, at the next vendored sync.
- **Report layer re-vendored from ctdcast v0.4.0** ([#111](https://github.com/ocean-uhh/oceanarray/pull/111)): the `degree`/`degrees`/`°` alias moves into the vendored family tables (the duplicate package-side copy is removed; unit resolution is unchanged). The array-map graticule gains edge labels drawn inside the west/south edges, a bottom-right scale bar, and antimeridian handling for basin-scale tracks.
- **ADCP bin pressures are tilt-corrected for knock-down** ([#112](https://github.com/ocean-uhh/oceanarray/pull/112)): `compute_adcp_bin_pressure` reduces each bin's vertical range by `cos(θ)` (θ = instrument tilt from pitch/roll, `arccos(cos P_true·cos R)`) before converting to pressure, so a knocked-down profiler's bins are placed at their true depth; `bin_pressure` gains `tilt_correction` / `tilt_definition` attributes. When the firmware recorded earth coordinates with bin mapping on (RDI `EX` command), it has already tilt-placed the bins, so the correction is **not** applied (`tilt_correction = "none (firmware bin mapping)"`). Every current UHH deployment is earth + bin-mapping, so stored outputs are unchanged; the correction is exercised only by beam-coordinate input. Stage 1 surfaces `firmware_coordinate_system` / `firmware_bin_mapping` from the raw metadata for the guard.

### Added

- **Readers for files already processed by their originator** ([#108](https://github.com/ocean-uhh/oceanarray/pull/108)): `file_type: oceansites` (OceanSITES 1.3 netCDF, and the OSNAP variant with variables on `TIME`) and `file_type: rodb` (RAPID/BODC `.microcat`, `.edt`, `.lowedt`), in `oceanarray.tools.readers` (`read_oceansites`, `read_rodb`). Variables are renamed to oceanarray names; the originator's QC flags are kept as `<name>_qc_originator`; the originator's metadata as `originator_*` attributes. Velocities are geographic, so Stage 3 applies no rotation and no second magnetic declination (`originator_magnetic_declination`). New instrument class `currentmeter` for a processed point current meter. Tested on trimmed real OSNAP files (WHOI, GEOMAR, Scripps, RODB) in `tests/fixtures/raw/osnap/`.
- **`units_override`** ([#108](https://github.com/ocean-uhh/oceanarray/pull/108)) per instrument in the mooring YAML corrects a file whose units attribute is wrong; Stage 1 refuses an imported (`oceansites` / `rodb`) instrument whose values fall outside the registry's valid range for the stated units, naming this key.
- **`stack: false`** ([#108](https://github.com/ocean-uhh/oceanarray/pull/108)) per instrument in the mooring YAML keeps an instrument out of the stack and grid while it is still processed through stages 1-3 and reported (reference data); the stack names it in an `instruments_excluded` attribute. A low-passed file (`originator_low_passed`, e.g. RODB `.lowedt`) reaching the stack without it is omitted with an error naming the key.
- Unit spellings accepted: `decibar`, `meter/second`, `meter`, `psu` (practical salinity only), and RODB `cm s-1` (converted ×0.01 to `m s-1`).

### Deprecated

- `oceanarray.processors.units.canonicalise_units` → `to_canonical_units`, and `oceanarray.processors.units.format_units` → `oceanarray.config.unit_tables.unit_label`. The old names warn and are removed in 0.6.0.
- `oceanarray.parameters.UNIT_ALIASES` and `VARIABLE_UNIT_ALIASES`: the spellings now live in the shared units tables (`oceanarray.config.unit_tables.TABLES`, with `PSU` / `NTU` scoped by CF `standard_name`). Both names still resolve, rebuilt from those tables, with a warning; removed in 0.6.0.

### Breaking changes

- None. The NetCDF outputs and the public API (`oceanarray` CLI, `from oceanarray.reports import MooringReport`) are unchanged. The report HTML changes (masthead nav, inventory-table layout and type labels, `source:` captions); the committed report goldens are rebaselined to match.

## [0.5.0] — 2026-10-12

### Added

- **Serial-uniqueness validation** ([#101](https://github.com/ocean-uhh/oceanarray/pull/101)): `validate` refuses duplicate serials within a mooring, a blank serial, and the reserved names `stack`/`grid` (which would collide the per-instrument report filenames `{mooring}_{serial}_report.html`). A trailing `*` is stripped before every check — `1234` and `1234*` on one mooring are a duplicate, a serial that is only `*` is blank — and stays a diagram-only "pay attention to this sensor" annotation, never carried into filenames or nav labels.

### Changed

- **Report NetCDF-inventory appendix restyled** ([#103](https://github.com/ocean-uhh/oceanarray/pull/103)): the instrument, stack and grid reports share one inventory appendix — a Coordinates table (one row per coordinate variable: type, units, standard/long name, min/max, N valid, attributes), variables grouped by dimension set, QC companions shown inline as a green `(_QC)`, and global attributes split into named groups with a Type column; the per-page variable/scalar/globals templates collapse to one shared set, and the grid page gains a coordinates table it never had. The dune2 test fixture units were canonicalised in place (14 attribute edits, data byte-identical) and the hosted dune2 example suite regenerated. `demo_mooring.ipynb` is ported to `oceanarray.processors.process` and runs on the committed fixture from a clean checkout; `demo_step1.ipynb` is removed, and five old-API demo notebooks move to `notebooks/legacy/`.
- **Shared masthead nav** ([#100](https://github.com/ocean-uhh/oceanarray/pull/100)): the report masthead navigation is rebuilt from the shared vendored macro, and the six report-design files are re-vendored from ctdcast with a vendoring guard (`scripts/check_vendored.py`).
- **Canonical units at every stage** ([#102](https://github.com/ocean-uhh/oceanarray/pull/102)): one canonical spelling per quantity is now written to every file — `degree_Celsius`, `mS cm-1`, `dbar`, `m`, `m s-1`, `umol kg-1`, `g kg-1`, `kg m-3`, `1` (practical salinity and turbidity). A registry-scoped pass (`oceanarray.processors.units.canonicalise_units`) runs on the way out of every stage, rewriting the reader spellings (`degC`, `mS/cm`, `meters`, `db`, `S/m`→×10) to the canonical and recording any value conversion in `units_converted_from` / `units_conversion_factor`. Temperature variables carry CF-1.11 `units_metadata = "temperature: on_scale"`. Plot labels are derived from the canonical string by `format_units` (slash form: `mS/cm`, `m/s`, `kg/m³`, `µmol/kg`, `°C`, `PSU`, `NTU`).

- **Single netCDF writer** ([#98](https://github.com/ocean-uhh/oceanarray/pull/98)): the six write paths the processing stages used before now go through one `oceanarray.writers.write` seam. The files that were written uncompressed — the seasenselib-written stage files, whose compression parameters its writer silently discarded, and the bare-`to_netcdf` stage3 output — shrink 58–66% on the dune2 fixtures. The two that were already zlib level-5 compressed — the pressure-gridded and stacked outputs — grow about 1% as they move to the common level-4 setting. Decoded values, dtypes, attributes, and time encoding are unchanged; raw values are byte-identical, verified per stage against the old writer. The private `_get_netcdf_writer_params` methods are removed, including the `quantize` key, which was never a valid netCDF4 encoding parameter and never took effect.
- **Windows log encoding** ([#98](https://github.com/ocean-uhh/oceanarray/pull/98)): `stage1/2/3` open their per-stage log file as UTF-8. On Windows the default cp1252 encoding raised on the ADCP `BEAM→ENU` log message, which aborted the instrument and silently dropped it from the output.
- **`oceanarray.writers` exposed** ([#99](https://github.com/ocean-uhh/oceanarray/pull/99)): `import oceanarray as oa; oa.writers.write(ds, path)` now resolves after a bare import.
- **cftime time coordinates preserved** ([#99](https://github.com/ocean-uhh/oceanarray/pull/99)): a time coordinate decoded with `use_cftime=True` (object dtype, e.g. a non-standard calendar) now keeps its `units` and `calendar` on write instead of letting xarray re-guess the epoch from the data. Numpy `datetime64` time is still pinned to the 1970 epoch; only object-dtype cftime time is affected.
- **Python 3.13 supported** ([#99](https://github.com/ocean-uhh/oceanarray/pull/99)): added to the classifiers and promoted to a required (no longer allow-failure) CI leg.

### Breaking changes

- **Units spellings in output files changed** ([#102](https://github.com/ocean-uhh/oceanarray/pull/102)): every processed file now carries the canonical units spelling per quantity (above), so a reader matching the old strings (`degC`, `mS/cm`, `meters`, `S/m`, …) must adapt. An unrecognised or missing `units` on a registered variable now **stops that instrument** (`UnitsError`) rather than passing through silently — one bad instrument is logged with its file and skipped, the rest process; the stack still builds but records the omission in an `instruments_omitted` attribute and exits 1. The `dissolved_oxygen` registry entry is corrected to per-mass `umol kg-1` (standard_name `moles_of_oxygen_per_unit_mass_in_sea_water`); turbidity is dimensionless `1` (display label `NTU`). Conductivity in `S/m` is converted ×10 to `mS cm-1` by the units pass, replacing the old `conductivity_normalised_from` attribute with `units_converted_from` / `units_conversion_factor`.
- **`process` runs stage 3 by default** ([#102](https://github.com/ocean-uhh/oceanarray/pull/102)): `oceanarray process MOORING` with no `--stage` now runs stages 1, 2 and 3 (was 1 and 2). Pass `--stage 1 2` for the old raw→trim-only behaviour. The help text already described "stages 1-3"; the code now matches it.
- **Oxygen saturation / AOU recomputed on the correct basis** ([#102](https://github.com/ocean-uhh/oceanarray/pull/102)): `derive_oxygen_saturation` previously divided per-mass `dissolved_oxygen` by in-situ density as if it were per-volume, making `oxygen_saturation_pct` and `apparent_oxygen_utilization` ~2.5% low. It now uses a per-mass (`umol kg-1`) input directly and divides only a genuine per-volume (`umol L-1`) variable. Regenerated oxygen products change by the density factor; moorings without an oxygen sensor (the dune2 example) are unaffected.
- **Minimum Python raised to 3.11** ([#99](https://github.com/ocean-uhh/oceanarray/pull/99)): `requires-python` is now `>=3.11`; the 3.10 classifier and CI leg are dropped. Python 3.10 reached end-of-life, and the core dependencies (xarray, pandas, numpy, matplotlib, gsw, scipy) already require 3.11 — so a 3.10 install silently resolved years-old, untested versions of the whole stack.

- **Dead output helpers removed** ([#99](https://github.com/ocean-uhh/oceanarray/pull/99)): `oceanarray.tools.writers.save_dataset` and `save_OS_instrument` are deleted, and `oceanarray/tools/writers.py` with them. Neither had a working caller (only tests and a dead `if 0:` notebook cell); `save_dataset` also wrote `NETCDF4_CLASSIC`, a format nothing else produced. Use `oceanarray.writers.write`. Two legacy notebooks (`notebooks/legacy/demo_batch_instrument.ipynb`, `notebooks/legacy/demo_instrument_rdb.ipynb`) still reference `save_OS_instrument` and no longer run; they are not CI-executed and are left for the notebook refresh.
- **`None` attributes dropped, not blanked** ([#99](https://github.com/ocean-uhh/oceanarray/pull/99)): a `None`-valued attribute (global or per-variable) is now dropped with a warning instead of written as `""`. `UNK` belongs where a value is determined, not the write seam. On the dune2 fixtures no attribute is affected (0/84 globals are empty); this is a guardrail.
- **Time encoding pinned** ([#99](https://github.com/ocean-uhh/oceanarray/pull/99)): every datetime variable is written as `seconds since 1970-01-01` (float64, `proleptic_gregorian`) rather than xarray's per-file guess. Decoded instants change by at most 0.13 µs (float64 seconds have ~238 ns spacing at the 2026 epoch, so ≤119 ns rounding; ~0.5 µs by 2050) — negligible against clock drift, which is corrected in whole seconds. **Raw stored values change**, so a reader using `decode_times=False`/`decode_cf=False` sees different numbers. This also removes a pandas-version dependence in the units string (space vs `T`).

### Known issues

- **Empty-units turbidity files**: a registered variable with an empty `units` fails that one instrument at stage 1 — the handler logs `ERROR: <file>: <reason>`, skips the instrument, and the mooring completes with the others and a non-zero exit (the family rule). Six turbidity files on the drive are affected; the reader fix lands in 0.5.1.
- **dune2 test fixture stack/grid**: the committed `_stack.nc`/`_grid.nc` fixtures reference instruments not present in the committed raw and YAML — they were built from the full mooring before the fixture was trimmed to two instruments — so running `oceanarray run` on the fixture collapses the stack to those two. A known fixture inconsistency, rebuilt in 0.5.1.

## [0.4.0] — 2026-09-08

### Added

- **caldip calibration-dip stub** ([#92](https://github.com/ocean-uhh/oceanarray/pull/92)): `processors/caldip.py` carries the shared contract (constants, `normalize_serial`, `Offsets`) and a five-function interface for the planned front-of-Stage-3 correction. Stage 3 gains a `caldip_dir` argument that is a null action — it logs a warning and stamps a `caldip_applied` provenance attribute — until the correction lands.
- **PDF reports**: `report --pdf` renders a merged A4 PDF per mooring ([#69](https://github.com/ocean-uhh/oceanarray/pull/69)); `report --pdf-dir DIR` collects each mooring's PDF into one directory ([#90](https://github.com/ocean-uhh/oceanarray/pull/90)), which also sped up PDF rendering (~50 s → ~6 s).
- **Manifest-driven report system**: a section-manifest model and resolver ([#78](https://github.com/ocean-uhh/oceanarray/pull/78)) now drive the grid ([#79](https://github.com/ocean-uhh/oceanarray/pull/79)) and stack / instrument / mooring ([#80](https://github.com/ocean-uhh/oceanarray/pull/80)) report pages — per-page registries, automatic panel numbering, generated jump-nav, and defect-visible stubs.
- **`oceanarray run` report redirection** ([#88](https://github.com/ocean-uhh/oceanarray/pull/88)): `run` accepts `-o/--output-dir`, `--report-dir`, and `--sig-level`, threaded through to the report step, so the full pipeline can write a portable central report tree with custom grid isopycnal targets in one command.
- **Zenodo DOI** ([#68](https://github.com/ocean-uhh/oceanarray/pull/68)): concept-DOI badge in the README and `CITATION.cff`.
- A hosted, clickable dune2 example report suite in the docs ([#82](https://github.com/ocean-uhh/oceanarray/pull/82)).

### Changed

- **Report subsystem refactor**: vendored the shared design tokens, encoder, and CSS ([#71](https://github.com/ocean-uhh/oceanarray/pull/71)); added a golden-file regression net ([#72](https://github.com/ocean-uhh/oceanarray/pull/72)); renamed `report/` → `reports/` ([#73](https://github.com/ocean-uhh/oceanarray/pull/73)); reworked page templates ([#74](https://github.com/ocean-uhh/oceanarray/pull/74)); encoder / figure sizing ([#75](https://github.com/ocean-uhh/oceanarray/pull/75)); uniform layout fixes ([#76](https://github.com/ocean-uhh/oceanarray/pull/76), [#77](https://github.com/ocean-uhh/oceanarray/pull/77)); plot polish — colorbar ticks, cyclic current direction, T-S bounds, tilt panels ([#83](https://github.com/ocean-uhh/oceanarray/pull/83)); layout polish — shared title helper, trajectory y-labels, clock legend, rose whitespace ([#84](https://github.com/ocean-uhh/oceanarray/pull/84)); smaller files, updated fonts, and multi-panel pagination ([#70](https://github.com/ocean-uhh/oceanarray/pull/70)).
- **Packaging**: consolidated configuration into `pyproject.toml`, expanded the CI test matrix, and added a ruff lint gate ([#86](https://github.com/ocean-uhh/oceanarray/pull/86)); moved the dev install to pyproject extras ([#85](https://github.com/ocean-uhh/oceanarray/pull/85)); dropped the unused direct `dolfyn` dependency ([#81](https://github.com/ocean-uhh/oceanarray/pull/81)).
- **Docs**: PyPI-first install instructions ([#81](https://github.com/ocean-uhh/oceanarray/pull/81)); caldip documented as the planned optional front-of-Stage-3 correction ([#91](https://github.com/ocean-uhh/oceanarray/pull/91)).

### Fixed

- Cross-page links now resolve in the merged PDF — the document is built once and rendered once, with per-page anchor-id namespacing ([#89](https://github.com/ocean-uhh/oceanarray/pull/89)).
- Conservative QC-flag resampling fix, with unit tests added for the pressure, helpers, and qc paths ([#87](https://github.com/ocean-uhh/oceanarray/pull/87)).
- `process` / `report` / `run` `--help` no longer crash on Python 3.11 — an argparse usage-formatting `AssertionError` from a single-member mutually-exclusive group around the suppressed `--basedir` flag; the group is removed ([#88](https://github.com/ocean-uhh/oceanarray/pull/88)).

### Breaking changes

- **`oceanarray stack` / `oceanarray grid` removed** ([#88](https://github.com/ocean-uhh/oceanarray/pull/88)): the standalone subcommands (deprecated since 0.2.0) are deleted. Use `oceanarray process MOORING --stage stack grid` — all flags (`--dt`, `--dp`, `--pmin`, `--pmax`, `--force`, `--proc-dir`) carry over; `stack`/`grid` remain available as `--stage` tokens.
- **`oceanarray logsheet` removed** ([#88](https://github.com/ocean-uhh/oceanarray/pull/88)): the disabled subcommand is deleted (now an argparse "invalid choice" error, not a `DeprecationWarning`). Use the standalone [`logsheet`](https://github.com/eleanorfrajka/logsheet) package.
- **Python 3.9 dropped** ([#85](https://github.com/ocean-uhh/oceanarray/pull/85)): the minimum supported version is now 3.10.
- Internal: `oceanarray.cli.cmd_stub` renamed to `cmd_init`; `cmd_stack`, `cmd_grid`, `cmd_logsheet` deleted. Affects only code importing these functions directly; the `oceanarray init` command is unchanged.

---

## [0.3.0] — 2026-08-11

### Breaking changes

- **`oceanarray.logsheet` subpackage deleted** ([#66](https://github.com/ocean-uhh/oceanarray/pull/66)): the deprecated `logsheet` subpackage has been removed from the tree. `oceanarray logsheet` still emits a `DeprecationWarning` and exits 1. Use the standalone [`logsheet`](https://github.com/eleanorfrajka/logsheet) package: `pip install git+https://github.com/eleanorfrajka/logsheet`, then `logsheet build ...`.
- **`oceanarray run` skips grid when stack fails** ([#65](https://github.com/ocean-uhh/oceanarray/pull/65)): if the stack stage fails, the grid stage is now skipped rather than attempted on incomplete input. This matches `process()` behaviour.

### Bug fixes

- **Grid report N² computed at latitude 0**: the grid report derived buoyancy frequency (N²) at latitude 0° because its `_dms_to_deg` import still pointed at the removed `mooring_level` module and failed silently inside a broad `except`. The import now resolves from `utilities`, so N² uses the mooring's actual latitude.

### Refactoring

- **`STAGES` registry as single source of truth in CLI dispatch** ([#65](https://github.com/ocean-uhh/oceanarray/pull/65)): `process()` accepts a stage list (always applied in `STAGES` order); `cmd_process` and `cmd_run` route through a single `process()` call; argparse `choices` are derived from `STAGES`.
- **Plot/report hardening** ([#64](https://github.com/ocean-uhh/oceanarray/pull/64)): `render_b64` guard, `plt.style.context`, `vlabel()` helper, `SIGMA_GRID` fix, and a `VARIABLES` registry (21 entries with `valid_min`/`valid_max`).

### Documentation

- Updated developer docs (`project_structure.md`, `methods/standardisation.rst`, `methods/vertical_gridding.rst`, `methods/nortek_coordinate_transform.rst`, `legacy.rst`) to the current `oceanarray.processors.*` module layout after the reorg; fixed broken import examples and API references.

### Tests

- Golden Stage 1 tests, reader and `_array` helper tests, plus associated bug fixes and dead-code removal ([#63](https://github.com/ocean-uhh/oceanarray/pull/63)).

---

## [0.2.0] — 2026-08-07

### Breaking changes

- **`oceanarray logsheet` disabled**: calling `oceanarray logsheet` now emits a `DeprecationWarning` and exits 1. The `logsheet` subpackage remains in the tree but is unused (deleted in v0.3.0). Use the standalone [`logsheet`](https://github.com/eleanorfrajka/logsheet) package: `pip install git+https://github.com/eleanorfrajka/logsheet`, then `logsheet build ...`.
- **`--basedir` removed** ([#52](https://github.com/ocean-uhh/oceanarray/pull/52)): use `--raw-dir` and `--proc-dir` instead. See the [migration guide](https://ocean-uhh.github.io/oceanarray/migration.html).
- **`plotter.py` retired** ([#53](https://github.com/ocean-uhh/oceanarray/pull/53)): the monolithic `oceanarray/plotter.py` and all backward-compatibility re-export shims have been removed. Import from canonical modules (`oceanarray.plotters.current`, `oceanarray.plotters.timeseries`, etc.).
- **Subpackage reorganisation** ([#49](https://github.com/ocean-uhh/oceanarray/pull/49), [#59](https://github.com/ocean-uhh/oceanarray/pull/59)): processing modules moved from `oceanarray/instrument/` and `oceanarray/mooring/` into `oceanarray.processors.*`. Top-level `oceanarray` imports unchanged; internal paths have moved.

### New features

- **`--stage stack grid` in `oceanarray process`**: the `--stage` flag now accepts `stack` and `grid` in addition to `1`, `2`, `3`. Run the full pipeline as `oceanarray process MOORING --stage 1 2 3 stack grid`. New flags `--dt`, `--dp`, `--pmin`, `--pmax` added to `process` (same defaults as `oceanarray run`).
- **`process()` public API** ([#58](https://github.com/ocean-uhh/oceanarray/pull/58)): `oceanarray.process(mooring_yaml, proc_dir, stages=…)` runs any combination of Stage 1–3, stack, and grid in a single call. `oceanarray.STAGES` is the registry of available stages.
- **Array report** ([#48](https://github.com/ocean-uhh/oceanarray/pull/48)): `oceanarray report <array.yaml> --array` generates a multi-mooring HTML summary with smart rebuild (only re-renders sections whose NC files are newer than the existing report).
- **Wave / frequency diagnostics** ([#47](https://github.com/ocean-uhh/oceanarray/pull/47)): rotary power spectra, near-inertial band energy, and wave diagnostics added to ADCP instrument reports.
- **Release automation** ([#61](https://github.com/ocean-uhh/oceanarray/pull/61)): pushing a `v*` tag now auto-creates a draft GitHub release with PR-based release notes; publishing the draft triggers PyPI upload.

### Bug fixes

- **QC flags unified to OceanSITES table** ([#54](https://github.com/ocean-uhh/oceanarray/pull/54)): all QC flag values now use the standard OceanSITES convention (0 no QC, 1 good, 2 probably good, 3 probably bad, 4 bad, 9 missing). Previously mixed conventions caused flag collisions on merge.
- **Plot regularisation** ([#50](https://github.com/ocean-uhh/oceanarray/pull/50)): fixed irregular time-axis sampling in instrument report figures.
- **Turbidity `units=MISSING`** ([#61](https://github.com/ocean-uhh/oceanarray/pull/61)): stage1 now strips placeholder `units` strings (`"MISSING"`, `""`) written by the RBR RSK reader when the instrument database has no units entry, and emits a `UserWarning` so the operator can verify the sensor spec.

### Deprecations

- **`oceanarray stack`**: deprecated in favour of `oceanarray process MOORING --stage stack`. Emits `DeprecationWarning`. Will be removed in v0.3.0.
- **`oceanarray grid`**: deprecated in favour of `oceanarray process MOORING --stage grid`. Emits `DeprecationWarning`. Will be removed in v0.3.0.
- `file_type: rbr-hex-oa` remapped automatically to `rbr-hex` with a `DeprecationWarning`; update mooring YAMLs.
- `file_type: sbe-asc` remapped automatically to `sbe-ascii`.

### Refactoring

- Single-source variable registries in `parameters.py`; `validation.py` derives from it ([#51](https://github.com/ocean-uhh/oceanarray/pull/51)).
- Figure-returning shape for `report/_plots.py` ([#56](https://github.com/ocean-uhh/oceanarray/pull/56)); `draw_*` helpers relocated to `plotters/` ([#57](https://github.com/ocean-uhh/oceanarray/pull/57)).
- Type annotations backfilled on `hydrographic.py` and `temporal.py`; two ANN per-file-ignores removed from `pyproject.toml`.

### Tests

- Integration chain tests (stage1 → stage2 → stage3 → stack → grid) over committed `dune2_1_2026` fixtures ([#60](https://github.com/ocean-uhh/oceanarray/pull/60)).
- CI: integration job now installs seasenselib from PyPI; Windows and macOS unit jobs run seasenselib-free.

---

## [0.1.0] — 2026-07-27

First public release.

### Processing pipeline

- **Stage 1** — raw instrument files → CF-NetCDF: Sea-Bird SBE37 MicroCAT (`sbe-cnv`, `sbe-ascii`), Nortek Aquadopp (`nortek-raw`, `nortek-ascii`, `nortek-csv`), RBR Solo/Duet (`rbr-rsk`, `rbr-dat`), RDI WorkHorse ADCP (`rdi-raw`). Nortek BEAM→XYZ transformation matrix parsed from `.hdr` file and applied at stage 1; matrix stored in output for reproducibility.
- **Stage 2** — deployment trimming (YAML `deployment_time`/`recovery_time`) and linear clock-drift correction from recovery timestamps.
- **Stage 3** — QARTOD gross-range and spike QC on T/C/S/P; pressure interpolation (QC flag 8) for instruments without a pressure sensor; Aquadopp XYZ→ENU rotation using heading/pitch/roll + IGRF magnetic declination (`ppigrf`); tilt QC on velocity; potential density via `gsw`.

### Mooring-level commands

- `oceanarray stack` — resample all instruments to a common time axis, stack into a single `(time, N_LEVELS)` dataset ordered deep-first.
- `oceanarray grid` — linearly interpolate the stacked dataset onto a regular pressure grid (`--pmin`, `--pmax`, `--dp`).

### Reporting

- `oceanarray report` — self-contained HTML reports (all figures base64-embedded, offline-readable):
  - Mooring summary: deployment metadata, instrument pipeline status, QC flag breakdown, clock corrections, knockdown figures, clock alignment check.
  - Per-instrument pages (`--instruments`): full time series with QC markers, T-S diagram, current roses, data histograms, QC table, scalar metadata.
  - Stack report (`--stack`): pressure/T/S/velocity time series, T-S diagram, current roses, spacing histogram.
  - Grid report (`--grid`): T/S/density pcolormesh, N², velocity sections, T-S diagram, power spectra.
  - Array report (`--array`): position map, summary table with clickable links to mooring reports.

### CLI

- `oceanarray process` — run stage 1, 2, and/or 3 for a mooring or a single instrument (`--serial`).
- `oceanarray run` — complete pipeline in one command (process + stack + grid + report).
- `oceanarray validate` — check mooring YAML for missing fields and unknown instrument types.
- `oceanarray list` — print accepted `instrument:` names and `file_type:` values.
- `oceanarray logsheet` — generate PDF logsheets from YAML instrument inventory.

### Logsheets

- `oceanarray logsheet` subcommand generates LaTeX-rendered PDF deployment logsheets from a cruise configuration YAML.

### Notable design choices

- Data provenance: all processing parameters (QC thresholds, declination value, transformation matrix) stored as NetCDF attributes so any file can be reprocessed exactly from itself.
- Discrete colorbars throughout (≤ 20 levels, `BoundaryNorm`).
- CF-convention variable names and standard names throughout; OceanSITES QC flag values.
- `seasenselib` is used for raw file reading but is a separate install (`pip install seasenselib --no-deps` — see installation docs).
