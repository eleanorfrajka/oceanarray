# A stage file from before sensor records

`dune2_1_2026_2941_stage3.nc`: the dune2 MicroCAT (s/n 2941) stage-3 file as oceanarray 0.6.0 wrote it, with one
`SENSOR_TEMP_` / `SENSOR_CNDC_` variable per sensor instead of one `SENSOR_CTD_2941` record. The stack and the report
still read such files (stage files are not rewritten without `--force`); the tests use it to check that every
calibration is kept.
