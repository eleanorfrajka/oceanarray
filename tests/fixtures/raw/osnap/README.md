# OSNAP reader fixtures

Trimmed real files (480 records each) for the `oceansites` and `rodb` readers in
`oceanarray.readers`. One file per format and instrument type. Header and
global attributes are unchanged; only the time dimension is cut.

| File | Format | Instrument | Source |
|---|---|---|---|
| `OS_OSNAP_WHOI_CF7_202007_CM_500m.nc` | OceanSITES 1.3, no `_QC` variables | current meter | OSNAP, WHOI/SIO, mooring CF7 |
| `OS_OSNAP-10633_KPO_1111_K7_201408_D_MCTD_OSNAPstyleOceanSITES.nc` | OceanSITES 1.3 with `_QC` variables lacking `flag_meanings`; `PRES_QC` = 8 (interpolated) | MicroCAT | OSNAP, GEOMAR, mooring KPO_1111, via PANGAEA doi:10.1594/PANGAEA.896648 (2018 release), CC-BY-4.0 |
| `CF7_MCTD_2020_22225_50m.nc` | OSNAP netCDF, variables on `TIME`, `_QC` with `flag_meanings`; **`CNDC` labelled `S/m` but in mS/cm** | MicroCAT | OSNAP, Scripps, mooring CF7 |
| `rtwb1_01_2014_001.microcat` | RODB text; includes one `-9999` fill | MicroCAT | OSNAP, mooring rtwb1_01_2014 |
| `rtwb1_01_2014_11021.edt` | RODB text; `MagDeviation` applied; includes a real `-9.0` velocity | current meter | OSNAP, mooring rtwb1_01_2014 |

The OSNAP data were "collected and made freely available by the OSNAP project and
the national programs that contribute to it" (from the files' `citation` attribute).

## Running the pipeline on these files

The two RODB files form one mooring (a MicroCAT and a current meter). From the
repository root:

```bash
bash tests/fixtures/raw/osnap/run_example.sh
```

The script copies the two files into a raw tree, writes the mooring YAML, runs
`oceanarray validate` and `oceanarray run`, and prints the report path. Everything
goes under `outputs/osnap-demo/` (git-ignored); set `W` to write elsewhere. The
current meter's velocities leave stage 3 unrotated: the `.edt` header's
`MagDeviation` was already applied by the originator and is recorded as
`originator_magnetic_declination`. The two slices cover different weeks of 2014,
so the stack and grid have one instrument at any one time.

The netCDF files run the same way with `file_type: oceansites`. The Scripps
MicroCAT (`CF7_MCTD_2020_22225_50m.nc`) is refused at stage 1 until its
instrument entry carries `units_override: {conductivity: mS/cm}`, because the
file labels mS/cm values `S/m`.
