# OSNAP reader fixtures

Trimmed real files (480 records each) for the `oceansites` and `rodb` readers in
`oceanarray.readers`. One file per format and instrument type. Header and
global attributes are unchanged; only the time dimension is cut.

| File | Format | Instrument | Source |
|---|---|---|---|
| `OS_OSNAP_WHOI_CF7_202007_CM_500m.nc` | OceanSITES 1.3, no `_QC` variables | current meter | OSNAP, WHOI/SIO, mooring CF7 |
| `OS_OSNAP-10633_KPO_1111_K7_201408_D_MCTD_OSNAPstyleOceanSITES.nc` | OceanSITES 1.3 with `_QC` variables lacking `flag_meanings`; `PRES_QC` = 8 (interpolated) | MicroCAT | OSNAP, GEOMAR, mooring KPO_1111, via PANGAEA doi:10.1594/PANGAEA.896648 (2018 release), CC-BY-4.0 |
| `CF7_MCTD_2020_22225_50m.nc` | OSNAP netCDF, variables on `TIME`, `_QC` with `flag_meanings`; **`CNDC` labelled `S/m` but in mS/cm** | MicroCAT | OSNAP, Scripps, mooring CF7 |
| `M2_MC_2018_2020_SN14644_2190m_cut.nc` | OSNAP netCDF, flags in `<CODE>_flag` (no `_QC`), the "appears_*" table; 190 records (2019-01-04/06) holding 1, 4, 5 (`modified_as_a_result_of_qc`) and 9 | MicroCAT | OSNAP, WHOI, mooring egM2 2018, s/n 14644, cut from `M2_MC_2018_2020_SN14644_2190m.nc` |
| `os_osnap-ic1_201608_mctd_76m_cut.nc` | OSNAP OceanSITES netCDF, T/S/P only; the serial is only in the variables' `sensor_serial_number` (`12046.0`); 190 records (2016-09-08/10) | MicroCAT | OSNAP, NIOZ, mooring IC1 2016, s/n 12046 (in `../../catalog/sensor_catalog.yaml`), cut from `os_osnap-ic1_201608_mctd_76m.nc` |
| `OS_OSNAP_CF1_202007_ADCP_173m_cut.nc` | OceanSITES 1.3 ADCP profile: `UCUR`/`VCUR`/`WCUR`/`ECUR`, per-bin `PRES` and `DEPTH` on `TIME × BINDEPTH` (23 bins), head `PREST`, `HEADING`, `PITCH`, `ROLL`, `MAGVAR`; `sensor_orientation = upward` | upward ADCP | OSNAP, WHOI, mooring CF1 2020, s/n 1467 (per the file), TIME records 1000–1479 (2020-08-26/09-15) cut from `OS_OSNAP_CF1_202007_ADCP_173m.nc` as `ncks -d TIME,1000,1479` |
| `OS_OSNAP-UMM2_201407_ADCP_300m_cut.nc` | OceanSITES 1.3 ADCP profile; `BINDEPTH` on `TIME × BINDEPTH` (50 bins) has no `_FillValue` and marks missing depths 99999 (outside `valid_min` 0 / `valid_max` 12000), in every bin for 22 records and in the far bins throughout | upward ADCP | OSNAP, UMM2 mooring (rrM2 2014), Workhorse Quartermaster 150 kHz, TIME records 8400–8599 (2015-06-28/07-07) cut from `OS_OSNAP-UMM2_201407_ADCP_300m.nc`, variables and attributes unchanged |
| `os_osnap-ic1_201608_adcp_480m_cut.nc` | OceanSITES 1.3 ADCP profile; `PERG`, `EAMP`, `CMAG` on `TIME × BEAM × BINDEPTH` (4 beams, 41 bins), `BINDEPTH` on `TIME × BINDEPTH`, head `PRES` on `TIME × INSTRDEPTH`; no global `sensor_orientation` | upward ADCP | OSNAP, NIOZ, mooring IC1 2016, Workhorse Long Ranger 75 kHz s/n 3514, TIME records 15168–15215 (2018-05-03/05) cut from `os_osnap-ic1_201608_adcp_480m.nc`, variables and attributes unchanged |
| `nocm3_03_2016_20961_bin01.edt`, `_bin02.edt`, `_bin04.edt` | RODB text, one file per ADCP bin (`Z`, `U`/`V`/`W`, `EV`, `BEAMnSS`/`COR`/`PGP`); bin 03 left out on purpose; header `InstrDepth` (2662) is the design depth, the bins sit near 2500 m | downward ADCP | OSNAP, mooring egM3 2016, s/n 20961; data rows 1001–1480 of each file (`awk '/=/{print; next} {n++; if (n>1000 && n<=1480) print}'`) |
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
