#!/usr/bin/env bash
# Run oceanarray on the RODB pair in this directory (one mooring: a MicroCAT and a
# current meter). Run from the repository root:
#
#     bash tests/fixtures/raw/osnap/run_example.sh
#
# Writes under outputs/osnap-demo/ (git-ignored); set W to write elsewhere.
set -euo pipefail

F=tests/fixtures/raw/osnap
W=${W:-outputs/osnap-demo}
M=rtwb1_01_2014

mkdir -p "$W/raw/$M/microcat" "$W/raw/$M/currentmeter" "$W/proc/$M"
cp "$F/rtwb1_01_2014_001.microcat" "$W/raw/$M/microcat/"
cp "$F/rtwb1_01_2014_11021.edt" "$W/raw/$M/currentmeter/"

cat > "$W/proc/$M/$M.mooring.yaml" <<YAML
name: $M
waterdepth: 1600
latitude: 57 28.240 N
longitude: 012 42.300 W
deployment_time: '2014-07-17T12:00'
recovery_time: '2015-06-22T12:00'
clamp:
- serial: 11335
  instrument: microcat
  file_type: rodb
  filename: rtwb1_01_2014_001.microcat
  hab: 1500
- serial: 11021
  instrument: currentmeter
  file_type: rodb
  filename: rtwb1_01_2014_11021.edt
  hab: 1505
YAML

oceanarray validate "$W/proc/$M/$M.mooring.yaml"
oceanarray run "$M" --raw-dir "$W/raw" --proc-dir "$W/proc" --force \
    --dt-seconds 1200 --pmin 70 --pmax 110 --dp 10

echo "Report: $W/proc/$M/report/${M}_report.html"
