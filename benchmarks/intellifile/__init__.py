"""IntelliFile export adapter for the nightly OCR quality gate.

Roadmap priority 7: ingest evaluation exports produced by IntelliFile's own
OCR pipeline, normalize them to this repo's internal result format, compute
CER/WER with the repo's existing metrics code, and merge the results into
the gate run/report — one measurement across projects, no more per-tool
impressions.
"""

from benchmarks.intellifile.adapter import (
    FORMAT,
    REQUIRED_SAMPLE_FIELDS,
    VALID_LANGUAGES,
    compute_intellifile_metrics,
    convert_samples,
    load_intellifile_export,
    merge_into_gate_report,
    to_golden_dataset,
)

__all__ = [
    "FORMAT",
    "REQUIRED_SAMPLE_FIELDS",
    "VALID_LANGUAGES",
    "compute_intellifile_metrics",
    "convert_samples",
    "load_intellifile_export",
    "merge_into_gate_report",
    "to_golden_dataset",
]
