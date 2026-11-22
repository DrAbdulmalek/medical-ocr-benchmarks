"""Tests for the IntelliFile export adapter (roadmap P7 quality gate).

Covers: export loading/validation, conversion to the internal golden
test-case shape, metric computation parity with the repo's CER/WER
implementation, aggregation, gate-report merging, the gate CLI exit codes,
and the config/thresholds.yaml ``intellifile`` section.
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from benchmarks.datasets.golden import GoldenDataset
from benchmarks.intellifile import (
    compute_intellifile_metrics,
    convert_samples,
    load_intellifile_export,
    merge_into_gate_report,
    to_golden_dataset,
)
# The adapter re-exports the repo's existing CER/WER + Arabic normalization
# from src/benchmarks/metrics.py; tests use them directly to assert parity.
from benchmarks.intellifile.adapter import (
    character_error_rate,
    normalize_arabic,
    word_error_rate,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
GOOD_FIXTURE = REPO_ROOT / "data" / "fixtures" / "sample_intellifile_export.json"
BAD_FIXTURE = REPO_ROOT / "data" / "fixtures" / "sample_intellifile_export_bad.json"
GATE_SCRIPT = REPO_ROOT / "scripts" / "run_intellifile_gate.py"
THRESHOLDS_YAML = REPO_ROOT / "config" / "thresholds.yaml"


@pytest.fixture
def good_export():
    return load_intellifile_export(GOOD_FIXTURE)


@pytest.fixture
def report(good_export):
    return compute_intellifile_metrics(good_export)


def _normalized_texts(sample):
    """Replicate the adapter's preprocessing (Arabic folding) on raw texts."""
    reference, hypothesis = sample["reference_text"], sample["ocr_text"]
    if sample["language"] == "ar":
        return normalize_arabic(reference), normalize_arabic(hypothesis)
    return reference, hypothesis


class TestLoadIntellifileExport:
    def test_good_fixture_loads(self, good_export):
        assert good_export["format"] == "intellifile-eval-v1"
        assert good_export["generated_at"]
        assert 6 <= len(good_export["samples"]) <= 8

    def test_missing_file_raises_filenotfound(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            load_intellifile_export(tmp_path / "missing.json")

    def test_invalid_json_is_valueerror(self, tmp_path):
        bad = tmp_path / "broken.json"
        bad.write_text("{not valid json", encoding="utf-8")
        with pytest.raises(ValueError, match="not valid JSON"):
            load_intellifile_export(bad)

    def test_wrong_format_raises(self, tmp_path):
        bad = tmp_path / "wrong_format.json"
        bad.write_text(json.dumps({"format": "something-else", "samples": []}), encoding="utf-8")
        with pytest.raises(ValueError, match="format"):
            load_intellifile_export(bad)

    def test_bad_fixture_raises_valueerror(self):
        with pytest.raises(ValueError, match="format"):
            load_intellifile_export(BAD_FIXTURE)

    def test_missing_required_field_names_the_field(self, tmp_path):
        payload = {
            "format": "intellifile-eval-v1",
            "generated_at": "2026-11-21T23:40:00Z",
            "samples": [{"file": "a.png", "engine": "e", "reference_text": "x", "language": "ar"}],
        }
        bad = tmp_path / "missing_field.json"
        bad.write_text(json.dumps(payload), encoding="utf-8")
        with pytest.raises(ValueError, match="ocr_text"):
            load_intellifile_export(bad)

    def test_unknown_language_raises(self, tmp_path):
        payload = {
            "format": "intellifile-eval-v1",
            "generated_at": "2026-11-21T23:40:00Z",
            "samples": [{
                "file": "a.png", "engine": "e",
                "reference_text": "x", "ocr_text": "y", "language": "fr",
            }],
        }
        bad = tmp_path / "bad_lang.json"
        bad.write_text(json.dumps(payload), encoding="utf-8")
        with pytest.raises(ValueError, match="language"):
            load_intellifile_export(bad)

    def test_invalid_generated_at_raises(self, tmp_path):
        payload = {
            "format": "intellifile-eval-v1",
            "generated_at": "32nd of May",
            "samples": [{
                "file": "a.png", "engine": "e",
                "reference_text": "x", "ocr_text": "y", "language": "ar",
            }],
        }
        bad = tmp_path / "bad_date.json"
        bad.write_text(json.dumps(payload), encoding="utf-8")
        with pytest.raises(ValueError, match="generated_at"):
            load_intellifile_export(bad)

    def test_non_string_reference_raises(self, tmp_path):
        payload = {
            "format": "intellifile-eval-v1",
            "generated_at": "2026-11-21T23:40:00Z",
            "samples": [{
                "file": "a.png", "engine": "e",
                "reference_text": 42, "ocr_text": "y", "language": "ar",
            }],
        }
        bad = tmp_path / "bad_ref.json"
        bad.write_text(json.dumps(payload), encoding="utf-8")
        with pytest.raises(ValueError, match="reference_text"):
            load_intellifile_export(bad)


class TestConvertSamples:
    def test_converted_dataset_passes_golden_validation(self, good_export):
        dataset = to_golden_dataset(good_export)
        assert GoldenDataset().validate(dataset) is True

    def test_fields_map_to_internal_sample_shape(self, good_export):
        cases = convert_samples(good_export)
        assert len(cases) == len(good_export["samples"])
        for sample, case in zip(good_export["samples"], cases):
            assert case["id"] == f"{sample['engine']}:{sample['file']}"
            assert case["reference"] == sample["reference_text"]
            assert case["hypothesis"] == sample["ocr_text"]
            assert case["language"] == sample["language"]
            assert case["engine"] == sample["engine"]
            assert isinstance(case["reference"], str)
            assert isinstance(case["hypothesis"], str)


class TestComputeIntellifileMetrics:
    def test_per_sample_metrics_match_repo_cer_wer_exactly(self, good_export, report):
        """Adapter results must equal a direct call to the repo's CER/WER
        on the same (Arabic-normalized) texts."""
        assert len(report["results"]) == len(good_export["samples"])
        for sample, entry in zip(good_export["samples"], report["results"]):
            ref, hyp = _normalized_texts(sample)
            cer_value, char_errors, ref_chars = character_error_rate(ref, hyp)
            wer_value, word_errors, ref_words = word_error_rate(ref, hyp)
            assert entry["metrics"]["cer"] == cer_value
            assert entry["metrics"]["wer"] == wer_value
            assert entry["metrics"]["char_errors"] == char_errors
            assert entry["metrics"]["ref_chars"] == ref_chars
            assert entry["metrics"]["word_errors"] == word_errors
            assert entry["metrics"]["ref_words"] == ref_words

    def test_result_entries_use_internal_result_model(self, report):
        for entry in report["results"]:
            assert set(entry) == {"benchmark_name", "dataset", "metrics", "metadata"}
            assert entry["benchmark_name"] == "intellifile"
            assert entry["metadata"]["source"] == "intellifile-eval-v1"

    def test_cer_is_nonzero_for_typical_ocr_errors(self, report):
        assert report["overall"]["cer"] > 0
        assert report["overall"]["wer"] > 0

    def test_overall_aggregates_are_micro_averaged(self, report):
        results = report["results"]
        total_char_errors = sum(e["metrics"]["char_errors"] for e in results)
        total_ref_chars = sum(e["metrics"]["ref_chars"] for e in results)
        total_word_errors = sum(e["metrics"]["word_errors"] for e in results)
        total_ref_words = sum(e["metrics"]["ref_words"] for e in results)
        assert report["overall"]["cer"] == round(total_char_errors / total_ref_chars, 6)
        assert report["overall"]["wer"] == round(total_word_errors / total_ref_words, 6)

    def test_per_engine_aggregation_covers_all_samples(self, good_export, report):
        fixture_engines = {s["engine"] for s in good_export["samples"]}
        assert set(report["engines"]) == fixture_engines
        assert sum(a["samples"] for a in report["engines"].values()) == report["total_samples"]

    def test_english_language_path_matches_repo_cer_wer(self):
        """Non-Arabic samples go through the same repo functions untouched."""
        export = {
            "format": "intellifile-eval-v1",
            "generated_at": "2026-11-21T23:40:00Z",
            "samples": [{
                "file": "en_report_001.png",
                "engine": "intellifile-vision",
                "reference_text": "Chest X-ray: mild pleural effusion on the right.",
                "ocr_text": "Chest X-ray: mild pleural effuson on the right.",
                "language": "en",
            }],
        }
        report = compute_intellifile_metrics(export)
        entry = report["results"][0]
        cer_value, _, _ = character_error_rate(export["samples"][0]["reference_text"],
                                               export["samples"][0]["ocr_text"])
        wer_value, _, _ = word_error_rate(export["samples"][0]["reference_text"],
                                          export["samples"][0]["ocr_text"])
        assert entry["metrics"]["cer"] == cer_value
        assert entry["metrics"]["wer"] == wer_value


class TestMergeIntoGateReport:
    def test_merges_entries_and_summary(self, report):
        gate_results = {"suite_name": "medical-ocr-benchmark-suite", "results": [], "summary": {}}
        merged = merge_into_gate_report(gate_results, report)

        intellifile_entries = [r for r in merged["results"] if r["benchmark_name"] == "intellifile"]
        # one per engine + one overall
        assert len(intellifile_entries) == len(report["engines"]) + 1
        assert "intellifile" in merged["summary"]
        assert merged["summary"]["intellifile"]["overall"] == report["overall"]

    def test_does_not_mutate_inputs(self, report):
        gate_results = {"results": [{"benchmark_name": "correction", "metrics": {}}], "summary": {}}
        merged = merge_into_gate_report(gate_results, report)
        assert len(gate_results["results"]) == 1
        # 1 existing entry + one per engine (2) + one overall
        assert len(merged["results"]) == 4
        assert "intellifile" not in gate_results["summary"]


class TestThresholdsConfig:
    def test_intellifile_section_exists(self):
        with open(THRESHOLDS_YAML, "r", encoding="utf-8") as f:
            config = yaml.safe_load(f)
        assert config["intellifile"]["max_cer"] == 0.08
        assert config["intellifile"]["max_wer"] == 0.25

    def test_existing_sections_still_parse(self):
        with open(THRESHOLDS_YAML, "r", encoding="utf-8") as f:
            config = yaml.safe_load(f)
        assert config["global"]["max_cer"] == 0.15
        assert config["global"]["max_wer"] == 0.25
        assert "engines" in config and "languages" in config
        assert config["engines"]["paddleocr"]["max_cer"] == 0.12


class TestGateCli:
    def _run_gate(self, *args):
        return subprocess.run(
            [sys.executable, str(GATE_SCRIPT), *args],
            capture_output=True,
            text=True,
            cwd=str(REPO_ROOT),
            timeout=120,
        )

    def test_exit_zero_on_good_fixture(self):
        result = self._run_gate(str(GOOD_FIXTURE))
        assert result.returncode == 0, result.stdout + result.stderr
        assert "PASS" in result.stdout

    def test_exit_zero_with_default_config_thresholds(self):
        # Fixture CER/WER must sit under the config thresholds 0.08 / 0.25.
        result = self._run_gate(str(GOOD_FIXTURE))
        assert result.returncode == 0, result.stdout + result.stderr
        assert "0.08" in result.stdout and "0.25" in result.stdout

    def test_exit_one_on_tight_threshold(self):
        result = self._run_gate(str(GOOD_FIXTURE), "--fail-below", "cer=0.001")
        assert result.returncode == 1, result.stdout + result.stderr
        assert "FAIL" in result.stdout

    def test_exit_one_via_wer_threshold(self):
        result = self._run_gate(str(GOOD_FIXTURE), "--fail-below", "wer=0.001")
        assert result.returncode == 1, result.stdout + result.stderr

    def test_exit_two_on_malformed_fixture(self):
        result = self._run_gate(str(BAD_FIXTURE))
        assert result.returncode == 2, result.stdout + result.stderr
        assert "malformed" in result.stderr.lower()

    def test_exit_two_on_missing_file(self):
        result = self._run_gate(str(REPO_ROOT / "data" / "fixtures" / "nope.json"))
        assert result.returncode == 2

    def test_exit_two_on_bad_fail_below_syntax(self):
        result = self._run_gate(str(GOOD_FIXTURE), "--fail-below", "cer")
        assert result.returncode == 2

    def test_json_output_is_parseable_and_gate_aware(self):
        result = self._run_gate(str(GOOD_FIXTURE), "--json")
        assert result.returncode == 0, result.stdout + result.stderr
        payload = json.loads(result.stdout)
        assert payload["passed"] is True
        assert payload["thresholds"]["cer"] == 0.08
        assert payload["thresholds"]["wer"] == 0.25
        assert payload["source"] == "intellifile-eval-v1"
        assert payload["overall"]["cer"] > 0
