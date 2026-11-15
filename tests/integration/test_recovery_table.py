"""Integration test for the cruise-report recovery table.

``generate_recovery_table`` reads a mooring's stage1 + stage2 NetCDF times and
its mooring YAML, then writes a standalone HTML table used in cruise reports.
It is invoked by the CLI ``report`` subcommand behind the ``--cruise-table``
flag (not via ``MooringReport.generate``).  This runs it against the committed
``dune2_1_2026`` fixtures (microcat 2941, aquadopp 9920) and checks the HTML is
written and well-formed — a smoke test that catches template/render crashes and
missing-variable guards that only surface on a real NetCDF.
"""

import re

from oceanarray.reports._recovery_table import generate_recovery_table

MOORING = "dune2_1_2026"


class TestRecoveryTable:
    """generate_recovery_table writes a well-formed HTML table from fixtures."""

    def _generate(self, proc_root, out_path, force=True):
        return generate_recovery_table(
            mooring_name=MOORING,
            proc_dir=proc_root / MOORING,
            out_path=out_path,
            force=force,
        )

    def test_returns_output_path(self, proc_root_stage1_stage2, tmp_path):
        out = tmp_path / f"{MOORING}_recovery_table.html"
        result = self._generate(proc_root_stage1_stage2, out)
        assert result == out

    def test_file_written_nonempty(self, proc_root_stage1_stage2, tmp_path):
        out = tmp_path / f"{MOORING}_recovery_table.html"
        self._generate(proc_root_stage1_stage2, out)
        assert out.exists()
        assert out.stat().st_size > 0

    def test_contains_html_table(self, proc_root_stage1_stage2, tmp_path):
        out = tmp_path / f"{MOORING}_recovery_table.html"
        self._generate(proc_root_stage1_stage2, out)
        html = out.read_text(encoding="utf-8").lower()
        assert "<html" in html
        assert "<table" in html

    def test_contains_microcat_label(self, proc_root_stage1_stage2, tmp_path):
        # microcat 2941 → 'SBE37 2941' via _instrument_label; confirms rows built.
        out = tmp_path / f"{MOORING}_recovery_table.html"
        self._generate(proc_root_stage1_stage2, out)
        assert "SBE37" in out.read_text(encoding="utf-8")

    def test_skip_when_exists_and_not_force(self, proc_root_stage1_stage2, tmp_path):
        # Second call without force returns the existing path without regenerating.
        out = tmp_path / f"{MOORING}_recovery_table.html"
        self._generate(proc_root_stage1_stage2, out)
        again = self._generate(proc_root_stage1_stage2, out, force=False)
        assert again == out

    def test_masthead_is_the_shared_mooring_facts(
        self, proc_root_stage1_stage2, tmp_path
    ):
        # The recovery masthead shows the shared facts it always showed, in the
        # shared order and formats: position in degrees and minutes, depth with unit.
        out = tmp_path / f"{MOORING}_recovery_table.html"
        self._generate(proc_root_stage1_stage2, out)
        html = out.read_text(encoding="utf-8")
        meta = html[html.index('<dl class="meta-grid">') : html.index("</dl>")]
        labels = re.findall(r"<dt>(.*?)</dt>", meta)
        assert labels == [
            "Latitude",
            "Longitude",
            "Water depth",
            "Deployment",
            "Recovery",
            "Duration",
        ]
        assert "<dd>65° 43.91′ N</dd>" in meta
        assert "<dd>745&thinsp;m</dd>" in meta
        assert "<dd>13d 15h</dd>" in meta


class TestRecoveryTableMissingYaml:
    """A mooring directory with no YAML yields None, not a crash."""

    def test_missing_yaml_returns_none(self, tmp_path):
        (tmp_path / MOORING).mkdir()
        result = generate_recovery_table(
            mooring_name=MOORING,
            proc_dir=tmp_path / MOORING,
            out_path=tmp_path / "out.html",
            force=True,
        )
        assert result is None
