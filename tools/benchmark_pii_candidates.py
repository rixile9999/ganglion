"""Freeze and evaluate candidate classification against the original PII corpus.

The --ready flag declares that model/configuration selection using validation
has finished. A persistent hash audit is written BEFORE reading test records
or constructing a detector. Reusing the audit with changed weights/config is
an error. Test examples are never passed to an optimizer or a proposer as gold.
"""
from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import asdict
from datetime import datetime, timezone
import gzip
import hashlib
import json
from pathlib import Path
import tempfile
import time

from ganglion.domains.pii.candidates import CandidateConfig, prepare_candidates
from ganglion.domains.pii.types import Span, interpret
if __package__ in {None, ""}:
    from pii_baselines.compare import exact_metrics, score
else:
    from tools.pii_baselines.compare import exact_metrics, score

FROZEN_SNAPSHOT = Path("examples/pii/snapshots/2026-10-08-baseline")
FROZEN_COUNTS = {"train": 6000, "validation": 1500, "test": 1500}
FREEZE_NAME = "candidate-evaluation-freeze.json"
MODEL_SUFFIXES = {".safetensors", ".pt", ".bin"}


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as source:
        while block := source.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def _model_files(checkpoint):
    paths = [path for path in Path(checkpoint).rglob("*") if path.is_file() and
             (path.suffix in MODEL_SUFFIXES or path.name in {"model.json", "vocab.json", "adapter_config.json", "config.json"})]
    if not (Path(checkpoint) / "model.json").is_file() or not any(path.suffix in MODEL_SUFFIXES for path in paths):
        raise ValueError("a trained checkpoint with model.json and saved weights is required")
    return {str(path.relative_to(checkpoint)): sha256(path) for path in sorted(paths)}


def _source_checkpoint(checkpoint, metadata):
    value = metadata.get("source_checkpoint")
    if not value:
        return None
    path = Path(value)
    if not path.is_absolute() and not path.exists():
        path = Path(checkpoint).parent / path
    if not path.is_dir():
        raise ValueError("referenced source checkpoint is unavailable")
    return path


def freeze_checkpoint(checkpoint, manifest_path, *, ready=False):
    """Hash saved model/config and manifest claims without loading test text."""
    checkpoint, manifest_path = Path(checkpoint), Path(manifest_path)
    if not ready:
        raise ValueError("--ready is required after validation-only model selection has finished")
    metadata = json.loads((checkpoint / "model.json").read_text())
    manifest = json.loads(manifest_path.read_text())
    if metadata.get("ready") is not True or metadata.get("training_complete") is not True:
        raise ValueError("checkpoint is not declared ready after completed training")
    if metadata.get("checkpoint_selected_on", metadata.get("selection_split", "validation")) != "validation":
        raise ValueError("checkpoint must be selected on validation, never test")
    if metadata.get("test_used_for_tuning") is True:
        raise ValueError("checkpoint metadata reports test-driven tuning")
    for name, measured in metadata.get("training_splits_sha256", {}).items():
        if name not in {"train", "validation"} or measured != manifest["splits"][name]["sha256"]:
            raise ValueError("training or validation provenance does not match the frozen corpus")
    source_checkpoint = _source_checkpoint(checkpoint, metadata)
    artifacts = {"candidate": _model_files(checkpoint)}
    if source_checkpoint is not None:
        artifacts["source"] = _model_files(source_checkpoint)
    configuration = {"candidate_config": metadata.get("candidate_config", asdict(CandidateConfig())),
                     "max_tokens": metadata.get("max_tokens", 128),
                     "model_metadata_sha256": artifacts["candidate"]["model.json"],
                     "artifact_sha256": artifacts,
                     "dataset_manifest_sha256": sha256(manifest_path),
                     "splits": {name: {"count": part["count"], "sha256": part["sha256"]}
                                for name, part in manifest["splits"].items()},
                     "source_checkpoint": str(source_checkpoint) if source_checkpoint else None}
    implementation_root = Path(__file__).resolve().parents[1] / "ganglion" / "domains" / "pii"
    configuration["implementation_sha256"] = {name: sha256(implementation_root / name) for name in
                                                ("candidate_model.py", "candidates.py", "native.py", "types.py")
                                                if (implementation_root / name).is_file()}
    digest = hashlib.sha256(json.dumps(configuration, sort_keys=True).encode()).hexdigest()
    path = checkpoint / FREEZE_NAME
    if path.exists():
        audit = json.loads(path.read_text())
        if audit.get("configuration_sha256") != digest:
            raise ValueError("checkpoint, configuration, or dataset changed after the pre-test freeze")
        return audit
    audit = {"format": "ganglion-candidate-pre-test-freeze-v1", "frozen_at": datetime.now(timezone.utc).isoformat(),
             "configuration_sha256": digest, **configuration,
             "test_read_before_freeze": False, "test_forward_before_freeze": False,
             "selection_policy": "caller --ready declares completed validation-only selection; no optimization in this evaluator",
             "test_scope": "same development test previously measured for Ganglion v4; not a new untouched external benchmark"}
    with path.open("x", encoding="utf-8") as destination:
        destination.write(json.dumps(audit, indent=2))
    return audit


def load_frozen_test(data, audit):
    """The only main-run test reader; call after the on-disk freeze exists."""
    path = Path(data) / "test.jsonl"
    expected = audit["splits"]["test"]
    if sha256(path) != expected["sha256"]:
        raise ValueError("test data differs from the pre-test frozen manifest")
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    if len(rows) != expected["count"] or len({row["id"] for row in rows}) != len(rows):
        raise ValueError("frozen test count or document IDs are invalid")
    return rows


def _public_span(span, length):
    if isinstance(span, dict):
        value = Span(span["start"], span["end"], span.get("type", span.get("kind")), span.get("score"),
                     span.get("origin", "model"), span.get("probabilities", {}))
    else:
        value = span
    value.validate(length)
    return {"start": value.start, "end": value.end, "type": value.kind,
            "score": value.score, "probabilities": dict(value.probabilities)}


def _proposal_ir(batch):
    return {"complete": not batch.overflow, "overflow": batch.overflow, "proposed_count": batch.proposed_count,
            "candidates": [{"id": candidate.id, "start": candidate.start, "end": candidate.end,
                            "field_hint": candidate.field_hint, "type_hints": list(candidate.type_hints),
                            "extraction_sources": list(candidate.extraction_sources),
                            "value_characters": len(candidate.value), "left_context_characters": len(candidate.left),
                            "right_context_characters": len(candidate.right)} for candidate in batch.candidates]}


def _safe_diagnostics(value):
    """Do not copy an adapter's source/context/key fields into ordinary IR."""
    result = {}
    for name in ("candidates", "proposed_count", "overflow", "fallback_count", "context_characters", "source_characters",
                 "encoded_tokens", "output_candidates"):
        if name in value and type(value[name]) in {int, float, bool}:
            result[name] = value[name]
    sources = value.get("candidate_sources")
    if isinstance(sources, dict) and all(isinstance(key, str) and type(count) is int for key, count in sources.items()):
        result["candidate_sources"] = dict(sources)
    return result


def _synchronize(detector):
    if str(getattr(detector, "device", "")).startswith("cuda"):
        import torch
        torch.cuda.synchronize()


def capture_predictions(rows, detector, *, batch_size=64):
    """Model sees text only. Proposal rebuilding for diagnostics is untimed."""
    if batch_size < 1:
        raise ValueError("batch size must be positive")
    raw, final, ir = [], [], []
    elapsed_ms = inference_ms = interpretation_ms = proposal_audit_ms = 0.0
    config = getattr(detector, "candidate_config", None) or CandidateConfig(**detector.metadata.get("candidate_config", {}))
    finalizer = getattr(detector, "interpret", interpret)
    for start in range(0, len(rows), batch_size):
        batch = rows[start:start + batch_size]
        texts = [row["text"] for row in batch]
        _synchronize(detector)
        began = time.perf_counter()
        predictions = detector.detect_batch(texts) if hasattr(detector, "detect_batch") else [detector.detect(text) for text in texts]
        _synchronize(detector)
        inferred = time.perf_counter()
        if len(predictions) != len(batch):
            raise ValueError("detector returned a different number of documents")
        diagnostics = detector.get_batch_diagnostics() if hasattr(detector, "get_batch_diagnostics") else [{} for _ in batch]
        if len(diagnostics) != len(batch):
            raise ValueError("detector diagnostics do not align with the input batch")
        for row, spans, diagnostic in zip(batch, predictions, diagnostics, strict=True):
            interpreted, changes = finalizer(row["text"], spans)
            raw.append({"id": row["id"], "spans": [_public_span(span, len(row["text"])) for span in spans]})
            final.append({"id": row["id"], "spans": [_public_span(span, len(row["text"])) for span in interpreted]})
            ir.append({"id": row["id"], "diagnostics": _safe_diagnostics(diagnostic), "correction_count": len(changes)})
        _synchronize(detector)
        completed = time.perf_counter()
        inference_ms += (inferred - began) * 1000
        interpretation_ms += (completed - inferred) * 1000
        elapsed_ms += (completed - began) * 1000
        audited = time.perf_counter()
        for row, record in zip(batch, ir[start:start + len(batch)], strict=True):
            proposal = detector.propose(row["text"]) if hasattr(detector, "propose") else prepare_candidates(row["text"], config)
            record["proposal"] = _proposal_ir(proposal)
        proposal_audit_ms += (time.perf_counter() - audited) * 1000
    return raw, final, ir, {"documents": len(rows), "batch_size": batch_size,
                           "pipeline_ms": elapsed_ms, "proposal_encoder_classification_ms": inference_ms,
                           "interpretation_and_capture_ms": interpretation_ms,
                           "proposal_rebuild_for_audit_ms": proposal_audit_ms,
                           "mean_document_pipeline_ms": elapsed_ms / len(rows) if rows else None,
                           "scope": "proposal construction + shared encoder + candidate classifier + Interpreter; token generation absent; diagnostic proposal rebuild excluded",
                           "timing_caveat": "same process/device; concurrent GPU workloads may exist; not a mobile latency measurement"}


def _span_set(spans):
    return {(span["start"], span["end"], span["type"]) for span in spans}


def proposal_analysis(rows, ir, raw, final):
    ids = {row["id"] for row in rows}
    for capture in (ir, raw, final):
        if len(capture) != len(rows) or len({row["id"] for row in capture}) != len(rows) or {row["id"] for row in capture} != ids:
            raise ValueError("all candidate captures must align exactly with frozen document IDs")
    by_id = {record["id"]: record for record in ir}
    raw_by_id, final_by_id = ({row["id"]: row for row in capture} for capture in (raw, final))
    gold_count = exact = contained = overflow = 0
    causes, records, counts = Counter(), [], []
    for row in rows:
        record, raw_spans, final_spans = by_id[row["id"]], raw_by_id[row["id"]]["spans"], final_by_id[row["id"]]["spans"]
        proposal = record["proposal"]
        for item in proposal["candidates"]:
            if type(item["start"]) is not int or type(item["end"]) is not int or not 0 <= item["start"] < item["end"] <= len(row["text"]):
                raise ValueError("invalid captured candidate character range")
        ranges = {(item["start"], item["end"]) for item in proposal["candidates"]}
        counts.append(proposal["proposed_count"])
        overflow += bool(proposal["overflow"])
        raw_set, final_set, gold_set = _span_set(raw_spans), _span_set(final_spans), _span_set(row["spans"])
        misses = []
        for entity in sorted(gold_set):
            start, end, kind = entity
            has_exact = (start, end) in ranges
            has_containment = any(cs <= start and end <= ce for cs, ce in ranges)
            exact += has_exact
            contained += has_containment
            gold_count += 1
            if entity in final_set:
                continue
            if entity in raw_set:
                reason = "interpreter_regression"
            elif proposal["overflow"]:
                reason = "proposal_coverage_unavailable_overflow"
            elif has_exact:
                reason = "classifier_rejection_or_type_error"
            elif has_containment:
                reason = "proposal_boundary_mismatch"
            else:
                reason = "proposal_missed_entity"
            causes[reason] += 1
            misses.append({"start": start, "end": end, "type": kind, "reason": reason})
        stages = {}
        for name, predicted in (("raw", raw_set), ("interpreted", final_set)):
            strict_fp = predicted - gold_set
            ordinary_fp = sum(not any(ps < ge and pe > gs for gs, ge, _ in gold_set) for ps, pe, _ in strict_fp)
            stages[name] = {"exact": exact_metrics(gold_set, predicted), "strict_false_positives": len(strict_fp),
                            "ordinary_non_pii_false_positives": ordinary_fp,
                            "overlapping_boundary_or_type_false_positives": len(strict_fp) - ordinary_fp}
        records.append({"id": row["id"], "language": row.get("language", "unspecified"),
                        "proposal_count": proposal["proposed_count"], "proposal_complete": proposal["complete"],
                        "misses": misses, **stages})
    return {"gold_entities": gold_count, "exact_span_proposal_hits": exact,
            "exact_span_proposal_recall": exact / gold_count if gold_count else 1.0,
            "containment_proposal_hits": contained, "containment_proposal_recall": contained / gold_count if gold_count else 1.0,
            "overflow_documents": overflow, "coverage_is_lower_bound": bool(overflow),
            "proposal_mean": sum(counts) / len(counts) if counts else None, "proposal_max": max(counts, default=0),
            "proposal_min": min(counts, default=0), "final_miss_root_causes": dict(causes), "documents": records}


def _diagnostic_summary(ir):
    summary = {}
    for name in ("candidates", "proposed_count", "context_characters", "source_characters", "encoded_tokens", "output_candidates"):
        values = [row["diagnostics"][name] for row in ir if name in row["diagnostics"]]
        summary[name] = {"instrumented_documents": len(values), "total": sum(values) if values else None,
                         "mean": sum(values) / len(values) if values else None, "max": max(values) if values else None}
    summary["fallback_count"] = sum(row["diagnostics"].get("fallback_count", 0) for row in ir)
    summary["overflow_documents"] = sum(bool(row["diagnostics"].get("overflow")) for row in ir)
    sources = Counter()
    for row in ir:
        sources.update(row["diagnostics"].get("candidate_sources", {}))
    summary["candidate_sources"] = dict(sources)
    summary["generated_output_tokens"] = 0
    summary["token_caveat"] = "encoded_tokens measures actual compiled input only when instrumented; dense short text may not reduce tokens"
    return summary


def evaluate_captures(rows, raw, final, ir, *, include_examples=False):
    analysis = proposal_analysis(rows, ir, raw, final)
    raw_score, final_score = (score(rows, capture, include_examples=include_examples) for capture in (raw, final))
    return {"raw_classifier": raw_score, "classifier_with_interpreter": final_score,
            "proposal_quality": {key: value for key, value in analysis.items() if key != "documents"},
            "document_diagnostics": analysis["documents"], "input_statistics": _diagnostic_summary(ir),
            "raw_false_positive_definition": "strict FP includes every non-exact typed span; ordinary FP means no overlap with any gold PII",
            "structured_input": "union of bounded candidate contexts plus typed candidate feature tensors; position tensors are not generated JSON tokens"}


def _capture_jsonl(path, rows):
    Path(path).write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")


def _baseline_capture(snapshot, test_hash):
    metadata = json.loads((snapshot / "ganglion-lora.metadata.json").read_text())
    if metadata["test_sha256"] != test_hash:
        raise ValueError("saved v4 baseline does not describe the same test corpus")
    with gzip.open(snapshot / "ganglion-lora.predictions.jsonl.gz", "rt", encoding="utf-8") as source:
        text = source.read()
    if hashlib.sha256(text.encode()).hexdigest() != metadata["prediction_sha256"]:
        raise ValueError("saved v4 baseline changed since capture")
    return [json.loads(line) for line in text.splitlines()], metadata


def controlled_baseline_runtime(rows, detector, *, batch_size=64):
    """Re-run shared frozen v4 weights with gold-free packing in this process."""
    from ganglion.domains.pii.train import pack_tokens, predict_tokens

    if not hasattr(detector, "encoder"):
        raise ValueError("controlled runtime requires the classifier's shared source encoder")
    encoder = detector.encoder
    inputs = [{"id": row["id"], "text": row["text"], "spans": []} for row in rows]
    _synchronize(encoder)
    began = time.perf_counter()
    packed = pack_tokens(encoder.tokenizer, inputs, encoder.device, encoder.metadata["boundary_classes"])
    predictions = predict_tokens(encoder.backbone, encoder.heads, packed, encoder.metadata, batch_size=batch_size)
    capture = [{"id": row["id"], "spans": [_public_span(span, len(row["text"])) for span in interpret(row["text"], spans)[0]]}
               for row, spans in zip(rows, predictions, strict=True)]
    _synchronize(encoder)
    elapsed = (time.perf_counter() - began) * 1000
    tokens = packed[0]["attention_mask"].sum(-1).tolist()
    return capture, {"pipeline_ms": elapsed, "mean_document_pipeline_ms": elapsed / len(rows) if rows else None,
                     "batch_size": batch_size, "encoded_tokens_total": sum(tokens),
                     "encoded_tokens_mean": sum(tokens) / len(tokens) if tokens else None,
                     "encoded_tokens_max": max(tokens, default=0),
                     "generated_output_tokens": 0,
                     "scope": "full-text tokenizer packing + same shared frozen backbone/readout + BIO decoding + standard Interpreter",
                     "timing_caveat": "same process/device and batch size; model-load cost excluded; concurrent work and first-call warmup may affect latency"}


def _fixture_rows(rows, entity_budget):
    if entity_budget < 1:
        raise ValueError("long-document entity budget must be positive")
    selected = {0: ()}
    for index, row in enumerate(rows):
        count = len(row["spans"])
        if not count:
            continue
        for previous, choices in list(selected.items()):
            next_count = previous + count
            if next_count <= entity_budget and next_count not in selected:
                selected[next_count] = (*choices, index)
        if entity_budget in selected:
            return [rows[index] for index in selected[entity_budget]]
    raise ValueError("frozen test rows cannot form the requested whole-paragraph entity count")


def long_document_benchmark(rows, detector, *, entity_budget=120):
    from ganglion.adapters.recovery import generate_keypair, restore_document
    from ganglion.domains.pii.pipeline import run_document

    chosen = _fixture_rows(rows, entity_budget)
    text = "\r\n\r\n".join(row["text"] for row in chosen) + "\r\n"
    gold, offset = [], 0
    for row in chosen:
        gold.extend({"start": offset + span["start"], "end": offset + span["end"], "type": span["type"]} for span in row["spans"])
        offset += len(row["text"]) + 4
    keys = generate_keypair()
    started = time.perf_counter()
    with tempfile.TemporaryDirectory(prefix="ganglion-candidate-long-") as temporary:
        root = Path(temporary)
        source = root / "source.txt"
        original = text.encode()
        source.write_bytes(original)
        config = {"strategy": "semantic", "window_chars": 256, "overlap_chars": 64, "max_tokens": 128}
        result = run_document(source, root / "output", detector, public_key=keys["public_key"], document_id="candidate-long-benchmark", **config)
        byte_to_character = {0: 0}
        byte_offset = 0
        for index, character in enumerate(text):
            byte_offset += len(character.encode())
            byte_to_character[byte_offset] = index + 1
        predictions = [{"start": byte_to_character[edit["source_span"][0]], "end": byte_to_character[edit["source_span"][1]], "type": edit["type"]}
                       for edit in map(json.loads, (root / "output" / "plan.jsonl").read_text().splitlines())]
        restored = root / "restored.txt"
        restored_info = restore_document(root / "output" / "document.txt", root / "output" / "recovery.bin", keys["private_key"], restored)
        exact_roundtrip = restored.read_bytes() == original
        if not exact_roundtrip:
            raise ValueError("long-document recovery did not preserve original bytes")
        quality = score([{"id": "long-document", "text": text, "spans": gold}], [{"id": "long-document", "spans": predictions}])
        return {"fixture": "whole paragraphs selected deterministically from the already frozen test capture",
                "fixture_document_ids": [row["id"] for row in chosen], "gold_entities": len(gold),
                "input_bytes": len(original), "characters": len(text), "preprocessor": config,
                "quality": quality, "pipeline": result, "exact_byte_roundtrip": exact_roundtrip,
                "restored_edits": restored_info["edits"], "total_including_restore_ms": (time.perf_counter() - started) * 1000,
                "keys": "ephemeral memory-only; no private key persisted", "outputs": "private temporary artifacts removed after verification"}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--ready", action="store_true")
    parser.add_argument("--data", type=Path, default=Path("runs/pii/diverse-v1"))
    parser.add_argument("--snapshot", type=Path, default=FROZEN_SNAPSHOT)
    parser.add_argument("--output", type=Path, default=Path("runs/pii/candidate-v1/comparison.json"))
    parser.add_argument("--device", default=None)
    parser.add_argument("--batch-size", type=int, default=64)
    parser.add_argument("--long-document-entities", type=int, default=0)
    parser.add_argument("--include-examples", action="store_true")
    parser.add_argument("--baseline-runtime", action="store_true", help="measure the shared v4 encoder in this process as well as the historical capture")
    args = parser.parse_args(argv)
    if args.batch_size < 1 or args.long_document_entities < 0:
        parser.error("invalid batch size or long-document entity count")
    manifest_path = args.data / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    reference = json.loads((args.snapshot / "manifest.json").read_text())
    for name, expected_count in FROZEN_COUNTS.items():
        if manifest["splits"][name]["count"] != expected_count or manifest["splits"][name]["sha256"] != reference["splits"][name]["sha256"]:
            raise ValueError("candidate benchmark requires the original frozen 6000/1500/1500 corpus")
    if args.include_examples and "fabricated synthetic" not in manifest.get("provenance", ""):
        raise ValueError("source excerpts require explicitly fabricated synthetic provenance")
    audit = freeze_checkpoint(args.checkpoint, manifest_path, ready=args.ready)
    from ganglion.domains.pii.candidate_model import CandidateDetector
    loaded = time.perf_counter()
    detector = CandidateDetector(args.checkpoint, device=args.device)
    load_ms = (time.perf_counter() - loaded) * 1000
    rows = load_frozen_test(args.data, audit)
    raw, final, ir, timing = capture_predictions(rows, detector, batch_size=args.batch_size)
    measured = evaluate_captures(rows, raw, final, ir, include_examples=args.include_examples)
    baseline, baseline_metadata = _baseline_capture(args.snapshot, audit["splits"]["test"]["sha256"])
    runtime_capture = None
    report = {"format": "ganglion-candidate-benchmark-v1", "freeze": audit, "model_load_ms": load_ms,
              "test_sha256": audit["splits"]["test"]["sha256"], "documents": len(rows),
              "protocol": {"model_selection": "validation only; no optimizer or rule tuning in this runner",
                           "test_status": "reused development benchmark, not an unseen real-document generalization result",
                           "baseline_training": "v4 frozen backbone/readout; candidate classifier adds separately validation-selected training",
                           "latency_comparison": "prior baseline timing is historical; no unconditional speed or token reduction claim",
                           "generation_output_tokens": 0, "source_examples": args.include_examples},
              "baseline_v4": {"snapshot": str(args.snapshot), "metadata": baseline_metadata, **score(rows, baseline, include_examples=args.include_examples)},
              "candidate": {**measured, "timing": timing}}
    if args.baseline_runtime:
        runtime_capture, runtime_timing = controlled_baseline_runtime(rows, detector, batch_size=args.batch_size)
        report["baseline_v4_runtime"] = {**score(rows, runtime_capture, include_examples=args.include_examples), "timing": runtime_timing}
        historical = {row["id"]: _span_set(row["spans"]) for row in baseline}
        report["baseline_v4_runtime"]["documents_differing_from_historical_export"] = sum(
            _span_set(row["spans"]) != historical[row["id"]] for row in runtime_capture)
        compiled = measured["input_statistics"]["encoded_tokens"]
        total = compiled["total"] if compiled["instrumented_documents"] == len(rows) else None
        report["candidate"]["encoded_token_ratio_to_full_text"] = (total / runtime_timing["encoded_tokens_total"]
                                                                    if total is not None and runtime_timing["encoded_tokens_total"] else None)
    if args.long_document_entities:
        report["long_document"] = long_document_benchmark(rows, detector, entity_budget=args.long_document_entities)
    # Reject accidental post-freeze calibration/weight/config changes as well.
    if freeze_checkpoint(args.checkpoint, manifest_path, ready=True)["configuration_sha256"] != audit["configuration_sha256"]:
        raise ValueError("model changed during frozen inference")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    _capture_jsonl(args.output.with_name("candidate.raw.predictions.jsonl"), raw)
    _capture_jsonl(args.output.with_name("candidate.interpreted.predictions.jsonl"), final)
    _capture_jsonl(args.output.with_name("candidate.ir.jsonl"), ir)
    report["capture_sha256"] = {name: sha256(args.output.with_name(name)) for name in
                                ("candidate.raw.predictions.jsonl", "candidate.interpreted.predictions.jsonl", "candidate.ir.jsonl")}
    if runtime_capture is not None:
        path = args.output.with_name("baseline-v4-runtime.predictions.jsonl")
        _capture_jsonl(path, runtime_capture)
        report["capture_sha256"][path.name] = sha256(path)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"documents": len(rows), "baseline_f1": report["baseline_v4"]["exact"]["f1"],
                      "candidate_raw_f1": measured["raw_classifier"]["exact"]["f1"],
                      "candidate_interpreted_f1": measured["classifier_with_interpreter"]["exact"]["f1"],
                      "proposal_recall": measured["proposal_quality"]["exact_span_proposal_recall"],
                      "output": str(args.output)}), flush=True)
    return report


if __name__ == "__main__":
    main()
