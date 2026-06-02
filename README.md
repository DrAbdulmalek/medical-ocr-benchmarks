# 🏥 Medical OCR Benchmarks

> Unified benchmark suite for the Medical OCR ecosystem — CER, WER, medical term accuracy, latency, and throughput.

[![CI](https://github.com/DrAbdulmalek/medical-ocr-benchmarks/actions/workflows/ci.yml/badge.svg)](https://github.com/DrAbdulmalek/medical-ocr-benchmarks/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

## Overview

**medical-ocr-benchmarks** provides a standardized framework for evaluating OCR engines, postprocessing correction, and PHI masking across the entire Medical OCR ecosystem. It ships with golden datasets in English, Arabic, and mixed-language, and generates publication-ready reports in Markdown, JSON, and HTML.

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                    medical-ocr-benchmarks                       │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  ┌─────────────┐    ┌──────────────┐    ┌─────────────────┐   │
│  │  Golden      │───▶│  Benchmark   │───▶│  Report         │   │
│  │  Datasets    │    │  Runner      │    │  Generator      │   │
│  │  (en/ar/mx)  │    │              │    │  (MD/JSON/HTML) │   │
│  └─────────────┘    └──────┬───────┘    └─────────────────┘   │
│                            │                                    │
│         ┌──────────────────┼──────────────────┐                 │
│         ▼                  ▼                  ▼                 │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────────┐     │
│  │ OCR Engines  │  │ Postprocessor│  │ Core Metrics     │     │
│  │              │  │              │  │                  │     │
│  │ • Tesseract  │  │ • Correction │  │ • CER / WER     │     │
│  │ • EasyOCR    │  │ • PHI Mask   │  │ • Edit Distance  │     │
│  │ • PaddleOCR  │  │              │  │ • Term Accuracy  │     │
│  │ • Surya      │  │              │  │ • Latency        │     │
│  └──────────────┘  └──────────────┘  └──────────────────┘     │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

## Quick Start

### Install

```bash
# Core dependencies only
pip install -e .

# Full install (with OCR engines)
pip install -e ".[full]"
```

### Run Benchmarks

```bash
# Run all benchmarks against all golden datasets
python scripts/run_all_benchmarks.py

# Specify output format
python scripts/run_all_benchmarks.py --format json

# Custom golden dataset directory
python scripts/run_all_benchmarks.py --golden-dir /path/to/golden

# Custom output directory
python scripts/run_all_benchmarks.py --output-dir ./my-results
```

### Use as a Library

```python
from benchmarks.core.metrics import EditDistance, MedicalTermEvaluator
from benchmarks.core.runner import BenchmarkRunner
from benchmarks.core.reporter import BenchmarkReporter

# Character Error Rate
result = EditDistance.cer(
    reference="Patient diagnosed with patent ductus arteriosus.",
    hypothesis="Patient diagnosed with patent ductus arteiosus."
)
print(f"CER: {result['cer']:.4f} (S={result['substitutions']}, D={result['deletions']}, I={result['insertions']})")

# Medical Term Evaluation
evaluator = MedicalTermEvaluator(["patent ductus arteriosus", "blood pressure"])
term_result = evaluator.evaluate(reference_text, hypothesis_text)
print(f"Medical term accuracy: {term_result['accuracy']:.2%}")

# Run full suite
runner = BenchmarkRunner()
results = runner.run_all()

# Generate reports
reporter = BenchmarkReporter(results)
reporter.save("results/benchmark_report", format="markdown")
reporter.save("results/benchmark_report", format="html")
```

## Benchmark Categories

| Category | Description | Metrics |
|----------|-------------|---------|
| **OCR Accuracy** | Tesseract, EasyOCR, PaddleOCR, Surya | CER, WER, medical term accuracy |
| **Correction** | Postprocessor spelling correction | Correction CER, phrase detection rate |
| **PHI Masking** | Protected Health Information detection | Detection recall, masking throughput |

## Core Metrics

### Character Error Rate (CER)

```python
from benchmarks.core.metrics import EditDistance

result = EditDistance.cer(
    reference="adenocarcinoma",
    hypothesis="adenocarcnoma"
)
# CER: 0.0769, Substitutions: 1, Deletions: 0, Insertions: 0
```

### Word Error Rate (WER)

```python
result = EditDistance.wer(
    reference="Patient diagnosed with hypertension",
    hypothesis="Patient diagnosed with hypertnsion"
)
# WER: 0.125, Substitutions: 1, Deletions: 0, Insertions: 0
```

### Medical Term Accuracy

```python
from benchmarks.core.metrics import MedicalTermEvaluator

evaluator = MedicalTermEvaluator(["adenocarcinoma", "FOLFOX", "chemotherapy"])
result = evaluator.evaluate(reference, hypothesis)
# accuracy: 0.667, found_exact: 2, found_approximate: 0, missing: 1
```

### Latency Profiling

```python
from benchmarks.core.metrics import LatencyProfiler

profiler = LatencyProfiler(warmup_runs=3, benchmark_runs=10)
timing = profiler.measure(some_ocr_function, image_path)
# mean, median, stdev, min, max, p95, p99
```

## Golden Dataset Format

Each golden dataset is a JSON file with the following structure:

```json
{
  "name": "en-medical-v1",
  "language": "en",
  "category": "medical",
  "test_cases": [
    {
      "id": "EN001",
      "category": "cardiology",
      "reference": "Ground truth text...",
      "hypothesis": "OCR output text...",
      "medical_terms": ["term1", "term2"],
      "ocr_confidence": 0.95,
      "phi_fields": ["patient_name"]
    }
  ]
}
```

### Included Datasets

| Dataset | Language | Cases | Specialties |
|---------|----------|-------|-------------|
| `en_medical.json` | English | 5 | Cardiology, Oncology, Pharmacology, Pulmonology, Neurology |
| `ar_medical.json` | Arabic | 5 | Cardiology, Oncology, Endocrinology, Orthopedics, Pharmacology |
| `mixed_medical.json` | Mixed | 3 | Cardiology, General, Radiology |

## Adding New Benchmarks

### 1. Create a benchmark class

```python
# benchmarks/ocr/my_engine.py
from benchmarks.core.metrics import EditDistance, LatencyProfiler

class MyEngineBenchmark:
    def run(self, golden_dataset: dict) -> dict:
        test_cases = golden_dataset.get("test_cases", [])
        # ... your evaluation logic ...
        return {"engine": "my_engine", "mean_cer": 0.05, ...}
```

### 2. Register with the runner

```python
runner = BenchmarkRunner()
runner.register("my_engine", "benchmarks.ocr.my_engine", "MyEngineBenchmark")
results = runner.run_single("my_engine", dataset_name="en_medical")
```

### 3. Add a new golden dataset

Place a JSON file in `data/golden/` following the format above.

## Results Format

Benchmark results are structured as:

```json
{
  "suite_name": "medical-ocr-benchmark-suite",
  "config": {...},
  "results": [
    {
      "benchmark_name": "correction",
      "dataset": "en_medical",
      "metrics": {...},
      "metadata": {...}
    }
  ],
  "summary": {
    "suite_name": "...",
    "total_benchmarks": 6,
    "metrics_summary": {...}
  }
}
```

## Related Repositories

| Repository | Description |
|-----------|-------------|
| [medical-ocr-postprocessor](https://github.com/DrAbdulmalek/medical-ocr-postprocessor) | Medical OCR text correction and normalization |
| [medical-ocr-data](https://github.com/DrAbdulmalek/medical-ocr-data) | Arabic and English medical OCR training data |

## License

MIT License — see [LICENSE](LICENSE) for details.

## Author

**Dr. Abdulmalek Tamer Al-husseini** — [drabdulmalek@proton.me](mailto:drabdulmalek@proton.me)
