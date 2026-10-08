"""Score captured baselines on one immutable corpus, without model inference.

Exact typed entity matching and union-mask coverage answer different questions.
No test-derived corrections, thresholds, or label mappings are applied here.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path

from ganglion.analyzer.domain_analysis import analyze_predictions
from ganglion.domains.pii.types import ENTITY_TYPES


def exact_metrics(gold, predicted):
    tp, fp, fn = len(gold & predicted), len(predicted - gold), len(gold - predicted)
    return {"tp": tp, "fp": fp, "fn": fn,
            "precision": tp / (tp + fp) if tp + fp else (0.0 if fn else 1.0),
            "recall": tp / (tp + fn) if tp + fn else 1.0,
            "f1": 2 * tp / (2 * tp + fp + fn) if 2 * tp + fp + fn else 1.0}


def _summarize(counts):
    tp, fp, fn = counts
    return {"tp": tp, "fp": fp, "fn": fn,
            "precision": tp / (tp + fp) if tp + fp else (0.0 if fn else 1.0),
            "recall": tp / (tp + fn) if tp + fn else 1.0,
            "f1": 2 * tp / (2 * tp + fp + fn) if 2 * tp + fp + fn else 1.0}


def _ranges(spans, length):
    result = []
    for span in spans:
        start, end = span["start"], span["end"]
        if type(start) is not int or type(end) is not int or not 0 <= start < end <= length:
            raise ValueError("invalid captured character range")
        result.append((start, end))
    return result


def mask_counts(text, gold, predicted):
    """Count union-covered characters; overlaps cannot double-count recall."""
    expected = {i for start, end in _ranges(gold, len(text)) for i in range(start, end)}
    masked = {i for start, end in _ranges(predicted, len(text)) for i in range(start, end)}
    covered = sum(all(i in masked for i in range(s["start"], s["end"])) for s in gold)
    covered_non_whitespace = sum(
        all(i in masked for i in range(s["start"], s["end"]) if not text[i].isspace())
        for s in gold)
    return Counter(gold_characters=len(expected), masked_characters=len(masked),
                   correctly_masked_characters=len(expected & masked),
                   overmasked_characters=len(masked - expected),
                   non_pii_characters=len(text) - len(expected),
                   fully_masked_entities=covered,
                   fully_masked_non_whitespace_entities=covered_non_whitespace,
                   gold_entities=len(gold))


def mask_metrics(counts):
    result = dict(counts)
    result.update(character_recall=counts["correctly_masked_characters"] / counts["gold_characters"] if counts["gold_characters"] else 1.0,
                  character_precision=counts["correctly_masked_characters"] / counts["masked_characters"] if counts["masked_characters"] else (0.0 if counts["gold_characters"] else 1.0),
                  fully_masked_entity_recall=counts["fully_masked_entities"] / counts["gold_entities"] if counts["gold_entities"] else 1.0,
                  fully_masked_non_whitespace_entity_recall=counts["fully_masked_non_whitespace_entities"] / counts["gold_entities"] if counts["gold_entities"] else 1.0,
                  non_pii_character_overmask_rate=counts["overmasked_characters"] / counts["non_pii_characters"] if counts["non_pii_characters"] else 0.0)
    return result


def trim(text, spans):
    """Shared whitespace normalization, with no punctuation or suffix repair."""
    result = []
    for span in spans:
        _ranges([span], len(text))
        if span["type"] not in ENTITY_TYPES:
            raise ValueError("baseline must declare a canonical five-type mapping")
        start, end = span["start"], span["end"]
        while start < end and text[start].isspace():
            start += 1
        while start < end and text[end - 1].isspace():
            end -= 1
        if start < end:
            result.append({"start": start, "end": end, "type": span["type"]})
    return result


def score(rows, predictions, *, include_examples=False):
    if len(predictions) != len(rows) or len({row["id"] for row in predictions}) != len(rows):
        raise ValueError("capture must contain one prediction per test document")
    by_id = {row["id"]: row for row in predictions}
    if set(by_id) != {row["id"] for row in rows}:
        raise ValueError("captured document IDs do not match the frozen corpus")
    totals, exported = [0, 0, 0], [0, 0, 0]
    per_type = {kind: [0, 0, 0] for kind in ENTITY_TYPES}
    per_language = defaultdict(lambda: [0, 0, 0])
    masks, native_masks, histogram = Counter(), Counter(), Counter()
    negatives = negative_fp = duplicate_count = pure_non_pii_predictions = 0
    examples = defaultdict(list)
    for row in rows:
        captured = by_id[row["id"]]
        final = trim(row["text"], captured["spans"])
        gold = {(s["start"], s["end"], s["type"]) for s in row["spans"]}
        predicted = {(s["start"], s["end"], s["type"]) for s in final}
        original = {(s["start"], s["end"], s["type"]) for s in captured["spans"]}
        duplicate_count += len(final) - len(predicted)
        for destination, measured in ((totals, predicted), (exported, original)):
            for index, values in enumerate((gold & measured, measured - gold, gold - measured)):
                destination[index] += len(values)
        for index, values in enumerate((gold & predicted, predicted - gold, gold - predicted)):
            per_language[row.get("language", "unspecified")][index] += len(values)
            for entity in values:
                per_type[entity[2]][index] += 1
        if not gold:
            negatives += 1
            negative_fp += bool(predicted)
        pure_non_pii_predictions += sum(not any(start < ge and end > gs for gs, ge, _ in gold)
                                       for start, end, _kind in predicted)
        masks.update(mask_counts(row["text"], row["spans"], final))
        raw = captured.get("card_normalized_model_spans", captured.get("raw_model_spans", captured["spans"]))
        native_masks.update(mask_counts(row["text"], row["spans"], raw))
        analysis = analyze_predictions("pii-text", None, final, row["spans"], document_length=len(row["text"]))
        histogram.update(analysis["histogram"])
        if include_examples:
            for failure in analysis["failures"]:
                kind = failure["kind"]
                if len(examples[kind]) >= 3:
                    continue
                examples[kind].append({"document_id": row["id"], "language": row.get("language"),
                                       "source": row["text"],
                                       "predicted": [{**s, "value": row["text"][s["start"]:s["end"]]} for s in failure["evidence"]["predicted"]],
                                       "expected": [{**s, "value": row["text"][s["start"]:s["end"]]} for s in failure["evidence"]["expected"]]})
    return {"documents": len(rows), "exact": _summarize(totals), "exported_exact": _summarize(exported),
            "per_type": {kind: _summarize(value) for kind, value in per_type.items()},
            "per_language": {language: _summarize(value) for language, value in sorted(per_language.items())},
            "mask_coverage": mask_metrics(masks), "native_all_label_mask_coverage": mask_metrics(native_masks),
            "negative_documents": negatives, "negative_documents_with_fp": negative_fp,
            "negative_document_fp_rate": negative_fp / negatives if negatives else None,
            "pure_non_pii_predictions": pure_non_pii_predictions,
            "duplicate_predictions": duplicate_count, "failure_histogram": dict(histogram),
            "examples": dict(examples) if include_examples else None}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=Path("runs/pii/diverse-v1/test.jsonl"))
    parser.add_argument("--captures", type=Path, default=Path("runs/pii/baselines"))
    parser.add_argument("--output", type=Path, default=Path("runs/pii/baselines/comparison.json"))
    parser.add_argument("--include-examples", action="store_true", help="include original text from explicitly synthetic corpus")
    args = parser.parse_args()
    test_hash = hashlib.sha256(args.data.read_bytes()).hexdigest()
    manifest = json.loads(args.data.with_name("manifest.json").read_text())
    if test_hash != manifest["splits"]["test"]["sha256"]:
        raise ValueError("test corpus changed since its manifest was created")
    if args.include_examples and "fabricated synthetic" not in manifest.get("provenance", ""):
        raise ValueError("source excerpts require an explicitly fabricated synthetic corpus")
    rows = [json.loads(line) for line in args.data.read_text().splitlines()]
    report = {"dataset": str(args.data), "test_sha256": test_hash, "recipe": manifest["recipe"],
              "documents": len(rows), "gold_entities": sum(len(row["spans"]) for row in rows),
              "protocol": {"coordinate": "Unicode character", "metric": "micro exact typed span F1",
                           "postprocessing": "exported native adapter then shared whitespace trim only",
                           "mask_metrics": "type-agnostic union of predicted character intervals",
                           "non_whitespace_entity_mask_recall": "all non-whitespace characters of a gold entity are covered; omitted separators alone do not imply disclosure",
                           "native_all_label_mask_metrics": "also includes native PII labels outside the five-type task",
                           "test_used_for_tuning": False, "timings_comparable": False,
                           "scope": "fabricated synthetic paragraphs; detection only, not restoration or real-document anonymity"},
              "models": {}}
    for path in sorted(args.captures.glob("*.predictions.jsonl")):
        name = path.name.removesuffix(".predictions.jsonl")
        metadata_path = path.with_name(name + ".metadata.json")
        metadata = json.loads(metadata_path.read_text())
        hashes = {metadata[key] for key in ("test_sha256", "data_sha256", "input_sha256") if key in metadata}
        if hashes != {test_hash}:
            raise ValueError(f"capture does not establish identical test data: {name}")
        prediction_hashes = {metadata[key] for key in ("prediction_sha256", "predictions_sha256") if key in metadata}
        if prediction_hashes and prediction_hashes != {hashlib.sha256(path.read_bytes()).hexdigest()}:
            raise ValueError(f"captured predictions changed since inference: {name}")
        captured = [json.loads(line) for line in path.read_text().splitlines()]
        measurements = score(rows, captured, include_examples=args.include_examples)
        profiles = sorted(set.intersection(*(set(row.get("profiles", {})) for row in captured)))
        measurements["adapter_profiles"] = {
            profile: score(rows, [{**row, "spans": row["profiles"][profile]} for row in captured])
            for profile in profiles}
        report["models"][name] = {"metadata": metadata, **measurements}
        print(json.dumps({"model": name, "f1": measurements["exact"]["f1"],
                          "full_entity_mask_recall": measurements["mask_coverage"]["fully_masked_entity_recall"]}))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
