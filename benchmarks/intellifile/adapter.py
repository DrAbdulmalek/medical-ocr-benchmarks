"""Adapter for IntelliFile OCR pipeline evaluation exports (intellifile-eval-v1).

Normalizes an IntelliFile export to this repo's internal sample/result
structures and computes CER/WER by calling the repo's EXISTING metrics code
(``src/benchmarks/metrics.py`` — the same functions the production gate uses
via ``src/benchmarks/runner.py:calculate_all_metrics``). No metric is
reimplemented here.

Export schema (``intellifile-eval-v1``)::

    {
      "format": "intellifile-eval-v1",
      "generated_at": "2026-11-22T00:00:00Z",      # ISO-8601 date/time
      "samples": [
        {
          "file": "radio_chest_014.png",            # non-empty str
          "engine": "intellifile-vision",           # non-empty str
          "reference_text": "...",                  # str (ground truth)
          "ocr_text": "...",                        # str (OCR output)
          "language": "ar"                          # "ar" | "en"
        }
      ]
    }

Malformed input raises :class:`ValueError` with a message identifying the
offending field/sample index.
"""

from __future__ import annotations

import importlib.util
import json
import statistics
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Repo metrics reuse (do NOT reimplement metrics here)
# ---------------------------------------------------------------------------


def _load_repo_metrics():
    """Import the repo's canonical metrics module.

    The CER/WER implementation with Arabic normalization lives in
    ``src/benchmarks/metrics.py`` and is what the nightly gate already uses.
    In a fully installed environment (``pip install -e .`` with the src
    layout) it is importable as ``benchmarks.metrics``; in a plain checkout
    the src package ``__init__`` needs that install, so we load the module
    file directly. Either way it is the same existing code — never a copy.
    """
    try:
        import benchmarks.metrics as module  # type: ignore

        return module
    except ModuleNotFoundError:
        pass

    module_path = Path(__file__).resolve().parents[2] / "src" / "benchmarks" / "metrics.py"
    if not module_path.is_file():
        raise ImportError(
            "Cannot locate the repo metrics module 'src/benchmarks/metrics.py'. "
            "Install the package (pip install -e .) or run from a repo checkout."
        )
    spec = importlib.util.spec_from_file_location("_intellifile_repo_metrics", module_path)
    module = importlib.util.module_from_spec(spec)
    # Register before executing: the module defines @dataclass classes and
    # dataclasses resolves cls.__module__ via sys.modules during class creation.
    sys.modules.setdefault(spec.name, module)
    spec.loader.exec_module(module)
    return module


_repo_metrics = _load_repo_metrics()

# Re-exported repo functions used to compute the gate metrics.
character_error_rate = _repo_metrics.character_error_rate
word_error_rate = _repo_metrics.word_error_rate
normalize_arabic = _repo_metrics.normalize_arabic
normalize_text = _repo_metrics.normalize_text

# ---------------------------------------------------------------------------
# Schema constants
# ---------------------------------------------------------------------------

FORMAT = "intellifile-eval-v1"
REQUIRED_SAMPLE_FIELDS = ("file", "engine", "reference_text", "ocr_text", "language")
VALID_LANGUAGES = ("ar", "en")
BENCHMARK_NAME = "intellifile"


# ---------------------------------------------------------------------------
# Loading and validation
# ---------------------------------------------------------------------------


def load_intellifile_export(path: str | Path) -> dict:
    """Load and validate an IntelliFile evaluation export JSON.

    Args:
        path: Path to an ``intellifile-eval-v1`` export file.

    Returns:
        The validated export dict (``format``, ``generated_at``, ``samples``).

    Raises:
        FileNotFoundError: If ``path`` does not exist.
        ValueError: If the file is not valid JSON or violates the
            documented schema (message names the offending field/sample).
    """
    path = Path(path)
    with open(path, "r", encoding="utf-8") as f:
        try:
            export = json.load(f)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"IntelliFile export '{path}' is not valid JSON: {exc}"
            ) from exc

    if not isinstance(export, dict):
        raise ValueError(
            f"IntelliFile export '{path}' must be a JSON object, "
            f"got {type(export).__name__}"
        )

    fmt = export.get("format")
    if fmt != FORMAT:
        raise ValueError(
            f"IntelliFile export '{path}' has unsupported 'format' {fmt!r}; "
            f"expected {FORMAT!r}"
        )

    generated_at = export.get("generated_at")
    if not isinstance(generated_at, str) or not generated_at.strip():
        raise ValueError(
            f"IntelliFile export '{path}': 'generated_at' must be a non-empty "
            f"ISO-8601 date string"
        )
    try:
        # 'Z' suffix is valid ISO-8601 but datetime.fromisoformat needs '+00:00' on py3.10
        datetime.fromisoformat(generated_at.replace("Z", "+00:00"))
    except ValueError:
        raise ValueError(
            f"IntelliFile export '{path}': 'generated_at' is not a valid "
            f"ISO-8601 date: {generated_at!r}"
        ) from None

    samples = export.get("samples")
    if not isinstance(samples, list) or not samples:
        raise ValueError(
            f"IntelliFile export '{path}': 'samples' must be a non-empty list"
        )

    for i, sample in enumerate(samples):
        _validate_sample(path, i, sample)

    return export


def _validate_sample(path: Path, index: int, sample: Any) -> None:
    """Validate a single export sample, raising ValueError on bad input."""
    where = f"IntelliFile export '{path}' sample[{index}]"
    if not isinstance(sample, dict):
        raise ValueError(f"{where} must be a JSON object, got {type(sample).__name__}")

    for field in REQUIRED_SAMPLE_FIELDS:
        if field not in sample:
            raise ValueError(f"{where} is missing required field '{field}'")

    for field in ("file", "engine"):
        if not isinstance(sample[field], str) or not sample[field].strip():
            raise ValueError(f"{where}.{field} must be a non-empty string")

    for field in ("reference_text", "ocr_text"):
        if not isinstance(sample[field], str):
            raise ValueError(
                f"{where}.{field} must be a string, got {type(sample[field]).__name__}"
            )

    language = sample["language"]
    if language not in VALID_LANGUAGES:
        raise ValueError(
            f"{where}.language must be one of {VALID_LANGUAGES}, got {language!r}"
        )


# ---------------------------------------------------------------------------
# Conversion to the repo's internal sample structure
# ---------------------------------------------------------------------------


def convert_samples(export: dict) -> list[dict]:
    """Map validated export samples to the internal golden test-case shape.

    Produces the dict shape validated by
    ``benchmarks.datasets.golden.GoldenDataset.validate()``: each test case
    carries ``id``, ``reference`` and ``hypothesis`` (strings), plus
    IntelliFile provenance metadata. Extra provenance keys are ignored by
    the golden validator, so converted samples can be fed straight into the
    existing benchmark plumbing.
    """
    cases = []
    for sample in export["samples"]:
        cases.append({
            "id": f"{sample['engine']}:{sample['file']}",
            "reference": sample["reference_text"],
            "hypothesis": sample["ocr_text"],
            "language": sample["language"],
            "category": BENCHMARK_NAME,
            "engine": sample["engine"],
            "source_file": sample["file"],
            "export_generated_at": export.get("generated_at"),
        })
    return cases


def to_golden_dataset(export: dict) -> dict:
    """Wrap converted samples as a golden-dataset dict (``name``/``test_cases``).

    The returned dict passes ``GoldenDataset.validate()`` and can therefore
    be consumed by :class:`benchmarks.core.runner.BenchmarkRunner` and the
    golden loaders without any modification.
    """
    return {
        "name": f"{BENCHMARK_NAME}_export",
        "language": "mixed",
        "test_cases": convert_samples(export),
    }


# ---------------------------------------------------------------------------
# Metrics (via the repo's existing CER/WER) and aggregation
# ---------------------------------------------------------------------------


def compute_intellifile_metrics(export: dict) -> dict:
    """Compute CER/WER for all samples and aggregate per engine + overall.

    Arabic samples are folded with the repo's ``normalize_arabic`` before
    the repo's ``character_error_rate`` / ``word_error_rate`` are called;
    the metric functions apply ``normalize_text`` themselves. English
    samples go through the same functions untouched (they normalize
    internally).

    Args:
        export: A validated export dict as returned by
            :func:`load_intellifile_export`.

    Returns:
        A report dict with ``source``, ``generated_at``, ``total_samples``,
        per-engine and ``overall`` aggregates (micro-averaged error rates
        plus macro means), and ``results`` — one entry per sample in the
        internal result model used by ``BenchmarkRunner.run_single`` /
        ``BenchmarkSuite.add_result`` (``benchmark_name``, ``dataset``,
        ``metrics``, ``metadata``).
    """
    results = []
    for case in convert_samples(export):
        reference, hypothesis = case["reference"], case["hypothesis"]
        if case["language"] == "ar":
            reference = normalize_arabic(reference)
            hypothesis = normalize_arabic(hypothesis)

        cer_value, char_errors, ref_chars = character_error_rate(reference, hypothesis)
        wer_value, word_errors, ref_words = word_error_rate(reference, hypothesis)

        # Internal result model: {"benchmark_name", "dataset", "metrics", "metadata"}
        results.append({
            "benchmark_name": BENCHMARK_NAME,
            "dataset": case["id"],
            "metrics": {
                "cer": cer_value,
                "wer": wer_value,
                "char_errors": char_errors,
                "ref_chars": ref_chars,
                "word_errors": word_errors,
                "ref_words": ref_words,
            },
            "metadata": {
                "engine": case["engine"],
                "language": case["language"],
                "source_file": case["source_file"],
                "source": FORMAT,
                "description": "IntelliFile OCR pipeline evaluation export",
            },
        })

    per_engine = _aggregate(results, key=lambda e: e["metadata"]["engine"])
    overall = _aggregate(results, key=lambda e: BENCHMARK_NAME)[BENCHMARK_NAME]

    return {
        "source": FORMAT,
        "generated_at": export.get("generated_at"),
        "total_samples": len(results),
        "engines": per_engine,
        "overall": overall,
        "results": results,
    }


def _aggregate(entries: list[dict], key) -> dict[str, dict]:
    """Aggregate result entries into per-group metric summaries.

    ``cer``/``wer`` are micro-averaged (total errors / total reference
    length), the standard corpus-level convention; ``*_macro_mean`` is the
    unweighted mean of per-sample rates. A group with zero reference
    characters/words reports ``None`` for that rate (undefined, not zero).
    """
    groups: dict[str, list[dict]] = {}
    for entry in entries:
        groups.setdefault(key(entry), []).append(entry)

    aggregated: dict[str, dict] = {}
    for group_name, group in groups.items():
        char_errors = sum(e["metrics"]["char_errors"] for e in group)
        ref_chars = sum(e["metrics"]["ref_chars"] for e in group)
        word_errors = sum(e["metrics"]["word_errors"] for e in group)
        ref_words = sum(e["metrics"]["ref_words"] for e in group)
        cer_values = [e["metrics"]["cer"] for e in group]
        wer_values = [e["metrics"]["wer"] for e in group]

        aggregated[group_name] = {
            "samples": len(group),
            "cer": round(char_errors / ref_chars, 6) if ref_chars else None,
            "wer": round(word_errors / ref_words, 6) if ref_words else None,
            "cer_macro_mean": round(statistics.mean(cer_values), 6),
            "wer_macro_mean": round(statistics.mean(wer_values), 6),
            "char_errors": char_errors,
            "ref_chars": ref_chars,
            "word_errors": word_errors,
            "ref_words": ref_words,
        }

    return aggregated


# ---------------------------------------------------------------------------
# Merge into the nightly gate report
# ---------------------------------------------------------------------------


def merge_into_gate_report(gate_results: dict, intellifile_report: dict) -> dict:
    """Merge an IntelliFile report into a ``run_all_benchmarks`` results dict.

    Appends one internal-format result entry per engine (plus an overall
    entry) to ``gate_results['results']`` and adds an ``intellifile`` block
    to ``gate_results['summary']``. The input dicts are not mutated.

    Args:
        gate_results: The final results dict produced by
            ``scripts/run_all_benchmarks.py`` (``results`` list + ``summary``).
        intellifile_report: Report dict from
            :func:`compute_intellifile_metrics`.

    Returns:
        A new merged results dict.
    """
    merged = dict(gate_results)
    results = list(gate_results.get("results", []))

    engines = intellifile_report.get("engines", {})
    for engine_name in sorted(engines):
        agg = engines[engine_name]
        results.append({
            "benchmark_name": BENCHMARK_NAME,
            "dataset": f"{BENCHMARK_NAME}:{engine_name}",
            "metrics": {
                "cer": agg["cer"],
                "wer": agg["wer"],
                "samples": agg["samples"],
            },
            "metadata": {
                "source": intellifile_report.get("source", FORMAT),
                "description": "IntelliFile OCR pipeline evaluation export",
            },
        })

    overall = intellifile_report.get("overall", {})
    if overall:
        results.append({
            "benchmark_name": BENCHMARK_NAME,
            "dataset": f"{BENCHMARK_NAME}:overall",
            "metrics": {
                "cer": overall["cer"],
                "wer": overall["wer"],
                "samples": overall["samples"],
            },
            "metadata": {
                "source": intellifile_report.get("source", FORMAT),
                "description": "IntelliFile OCR pipeline evaluation export",
            },
        })

    merged["results"] = results
    summary = dict(gate_results.get("summary", {}))
    summary[BENCHMARK_NAME] = {
        "source": intellifile_report.get("source", FORMAT),
        "generated_at": intellifile_report.get("generated_at"),
        "total_samples": intellifile_report.get("total_samples"),
        "overall": overall,
        "engines": engines,
    }
    merged["summary"] = summary
    return merged
