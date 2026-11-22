"""Gate CLI for IntelliFile OCR pipeline evaluation exports.

Roadmap priority 7: run the nightly-gate semantics over an IntelliFile
export. Computes CER/WER with the repo's existing metrics code
(via ``benchmarks.intellifile``) and exits non-zero when a threshold is
breached, so the export can gate the pipeline like any other benchmark.

Exit codes:
    0 — all thresholds pass
    1 — threshold breach (gate failure)
    2 — malformed input (bad JSON/schema, unreadable file, bad --fail-below)

Usage::

    python scripts/run_intellifile_gate.py <export.json> [--json] \
        [--fail-below cer=0.08] [--fail-below wer=0.25]
"""

import json
import os
import sys
from pathlib import Path

import click

# Ensure the project root is on the Python path (same pattern as
# scripts/run_all_benchmarks.py)
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from benchmarks.intellifile.adapter import (  # noqa: E402
    FORMAT,
    compute_intellifile_metrics,
    load_intellifile_export,
)

REPO_ROOT = Path(project_root)
DEFAULT_THRESHOLDS_PATH = REPO_ROOT / "config" / "thresholds.yaml"
FALLBACK_THRESHOLDS = {"cer": 0.15, "wer": 0.25}
METRIC_KEYS = ("cer", "wer")
# Error-rate metrics: lower is better, so a value strictly above the
# threshold breaches the gate.


def default_thresholds(config_path: Path = DEFAULT_THRESHOLDS_PATH) -> dict:
    """Read gate defaults from config/thresholds.yaml (``intellifile`` section).

    Falls back to conservative defaults if the config or PyYAML is
    unavailable; a missing section never breaks the gate.
    """
    thresholds = dict(FALLBACK_THRESHOLDS)
    try:
        import yaml

        if config_path.is_file():
            with open(config_path, "r", encoding="utf-8") as f:
                config = yaml.safe_load(f) or {}
            section = config.get("intellifile", {}) or {}
            if "max_cer" in section:
                thresholds["cer"] = float(section["max_cer"])
            if "max_wer" in section:
                thresholds["wer"] = float(section["max_wer"])
    except Exception:
        # Config parsing is best-effort; the explicit --fail-below flags and
        # the built-in fallbacks keep the gate usable in any environment.
        pass
    return thresholds


def parse_fail_below(pairs) -> dict:
    """Parse ``--fail-below metric=value`` pairs into a threshold dict.

    Raises:
        click.BadParameter: On malformed or unknown metric names.
    """
    parsed = {}
    for pair in pairs:
        raw = str(pair).strip()
        if "=" not in raw:
            raise click.BadParameter(
                f"'{pair}' must look like METRIC=VALUE (e.g. cer=0.08); "
                f"metrics: {', '.join(METRIC_KEYS)}"
            )
        metric, _, value = raw.partition("=")
        metric = metric.strip().lower()
        if metric not in METRIC_KEYS:
            raise click.BadParameter(
                f"unknown metric '{metric.strip()}'; metrics: {', '.join(METRIC_KEYS)}"
            )
        try:
            threshold = float(value.strip())
        except ValueError:
            raise click.BadParameter(
                f"threshold for '{metric}' must be a number, got {value.strip()!r}"
            ) from None
        if threshold < 0:
            raise click.BadParameter(f"threshold for '{metric}' must be >= 0")
        parsed[metric] = threshold
    return parsed


def evaluate_gate(report: dict, thresholds: dict) -> tuple[bool, list]:
    """Compare the overall metrics against thresholds.

    Returns:
        (passed, violations) where each violation is a dict with ``metric``,
        ``value`` and ``threshold``.
    """
    overall = report["overall"]
    violations = []
    for metric in METRIC_KEYS:
        threshold = thresholds.get(metric)
        if threshold is None:
            continue
        value = overall.get(metric)
        if value is None:
            continue  # undefined rate (no reference text) cannot breach
        if value > threshold:
            violations.append({
                "metric": metric,
                "value": value,
                "threshold": threshold,
            })
    return (not violations), violations


@click.command()
@click.argument(
    "export",
    type=click.Path(exists=True, dir_okay=False, readable=True),
)
@click.option("--json", "as_json", is_flag=True, help="Print the machine-readable JSON report.")
@click.option(
    "--fail-below",
    "fail_below",
    multiple=True,
    metavar="METRIC=VALUE",
    help="Gate threshold, e.g. cer=0.08 (repeatable). Defaults come from "
    "config/thresholds.yaml (intellifile: max_cer/max_wer). A gate fails when "
    "the metric exceeds the threshold.",
)
def main(export, as_json, fail_below):
    """Run the IntelliFile quality gate over an evaluation export.

    Computes CER/WER with the repo's existing metrics code and exits 1 when
    a threshold is breached, 2 on malformed input.
    """
    thresholds = default_thresholds()
    try:
        thresholds.update(parse_fail_below(fail_below))
    except click.BadParameter as exc:
        click.echo(click.style(f"ERROR: {exc.format_message()}", fg="red"), err=True)
        sys.exit(2)

    try:
        loaded = load_intellifile_export(export)
    except (ValueError, OSError) as exc:
        click.echo(click.style(f"ERROR: malformed IntelliFile input — {exc}", fg="red"), err=True)
        sys.exit(2)

    report = compute_intellifile_metrics(loaded)
    passed, violations = evaluate_gate(report, thresholds)
    report["thresholds"] = {m: thresholds[m] for m in METRIC_KEYS if m in thresholds}
    report["passed"] = passed
    report["violations"] = violations

    if as_json:
        click.echo(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        _print_report(export, report, thresholds, passed)

    sys.exit(0 if passed else 1)


def _print_report(export, report, thresholds, passed) -> None:
    """Print a small human-readable gate report."""
    click.echo(click.style("🩺 IntelliFile OCR quality gate", fg="cyan", bold=True))
    click.echo(f"   Export:     {export}")
    click.echo(f"   Format:     {report['source']} (generated_at: {report['generated_at']})")
    engines = report["engines"]
    click.echo(f"   Samples:    {report['total_samples']} across {len(engines)} engine(s)")

    overall = report["overall"]
    for metric in METRIC_KEYS:
        threshold = thresholds.get(metric)
        value = overall.get(metric)
        if value is None:
            click.echo(f"   Overall {metric.upper()}: undefined (no reference text)")
            continue
        status = click.style("ok", fg="green") if value <= threshold else click.style("BREACH", fg="red")
        click.echo(f"   Overall {metric.upper()}: {value:.4f} (threshold <= {threshold}) [{status}]")

    click.echo("   Per engine:")
    for engine_name in sorted(engines):
        agg = engines[engine_name]
        click.echo(
            f"     - {engine_name}: samples={agg['samples']}, "
            f"cer={agg['cer']}, wer={agg['wer']}, "
            f"macro_mean(cer)={agg['cer_macro_mean']}, macro_mean(wer)={agg['wer_macro_mean']}"
        )

    if passed:
        click.echo(click.style("Verdict: PASS", fg="green", bold=True))
    else:
        details = ", ".join(
            f"{v['metric']}={v['value']} > {v['threshold']}" for v in report["violations"]
        )
        click.echo(click.style(f"Verdict: FAIL ({details})", fg="red", bold=True))


if __name__ == "__main__":
    main()
