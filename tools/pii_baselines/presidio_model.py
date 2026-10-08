#!/usr/bin/env python3
"""Run official Presidio recognizers without accessing benchmark gold labels.

Use an isolated environment; this script does not install into Ganglion's ML env::

    python -m venv runs/pii/baselines/.venv-presidio
    runs/pii/baselines/.venv-presidio/bin/pip install presidio-analyzer
    runs/pii/baselines/.venv-presidio/bin/python -m spacy download en_core_web_lg
    runs/pii/baselines/.venv-presidio/bin/python -m spacy download ko_core_news_sm

The default run uses AnalyzerEngine's English defaults, including its original
phone regions. The separately labelled en-ko configuration uses official Korean
NER and recognizers, adds KR to phone regions, and unions language passes only
for rows whose existing language metadata says "mixed". No patterns, context
words or span boundary fixes are added from the test set.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import importlib.metadata
import inspect
import json
import os
from pathlib import Path
import platform
import resource
import statistics
import sys
import time


ENTITY_MAPPING = {
    "PERSON": "PERSON", "LOCATION": "ADDRESS",
    "PHONE_NUMBER": "PHONE", "EMAIL_ADDRESS": "EMAIL",
    "US_SSN": "IDENTIFIER", "US_ITIN": "IDENTIFIER", "US_PASSPORT": "IDENTIFIER",
    "US_DRIVER_LICENSE": "IDENTIFIER", "UK_NHS": "IDENTIFIER", "MEDICAL_LICENSE": "IDENTIFIER",
    "KR_RRN": "IDENTIFIER", "KR_FRN": "IDENTIFIER", "KR_DRIVER_LICENSE": "IDENTIFIER",
    "KR_PASSPORT": "IDENTIFIER", "KR_BRN": "IDENTIFIER",
}
OFFICIAL_SOURCES = {
    "installation": "https://microsoft.github.io/presidio/installation/",
    "language_configuration": "https://github.com/data-privacy-stack/presidio/blob/main/docs/tutorial/05_languages.md",
    "recognizer_defaults": "https://github.com/data-privacy-stack/presidio/blob/main/presidio-analyzer/presidio_analyzer/conf/default_recognizers.yaml",
    "rrn_validation": "https://github.com/data-privacy-stack/presidio/blob/main/presidio-analyzer/presidio_analyzer/predefined_recognizers/country_specific/korea/kr_rrn_recognizer.py",
    "email_validation": "https://github.com/data-privacy-stack/presidio/blob/main/presidio-analyzer/presidio_analyzer/predefined_recognizers/generic/email_recognizer.py",
    "reserved_domains": "https://www.iana.org/assignments/special-use-domain-names/",
    "korean_model": "https://spacy.io/models/ko#ko_core_news_sm",
    "english_model": "https://spacy.io/models/en#en_core_web_lg",
}


def file_hash(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as source:
        while block := source.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def build_engine(configuration):
    from presidio_analyzer import AnalyzerEngine, RecognizerRegistry
    from presidio_analyzer.nlp_engine import NerModelConfiguration, SpacyNlpEngine
    from presidio_analyzer.predefined_recognizers import (
        KrBrnRecognizer, KrDriverLicenseRecognizer, KrFrnRecognizer,
        KrPassportRecognizer, KrRrnRecognizer, PhoneRecognizer,
    )

    if configuration == "en-default":
        return AnalyzerEngine(), []
    ner_config = NerModelConfiguration()
    # These are the released Korean model's label definitions, not dataset rules.
    ner_config.model_to_presidio_entity_mapping.update(
        PS="PERSON", LC="LOCATION", OG="ORGANIZATION", DT="DATE_TIME", TI="DATE_TIME")
    ner_config.labels_to_ignore = list(ner_config.labels_to_ignore or []) + ["QT"]
    nlp_engine = SpacyNlpEngine(models=[
        {"lang_code": "en", "model_name": "en_core_web_lg"},
        {"lang_code": "ko", "model_name": "ko_core_news_sm"},
    ], ner_model_configuration=ner_config)
    nlp_engine.load()
    registry = RecognizerRegistry(supported_languages=["en", "ko"])
    registry.load_predefined_recognizers(languages=["en", "ko"], nlp_engine=nlp_engine)
    phone_regions = tuple(dict.fromkeys((*PhoneRecognizer().supported_regions, "KR")))
    additions = []
    # All supported languages can contain Korean telephone/national-ID formats;
    # locale recognition is distinct from NER language. This is explicit config.
    registry.remove_recognizer("PhoneRecognizer")
    for language in ("en", "ko"):
        registry.add_recognizer(PhoneRecognizer(supported_language=language, supported_regions=phone_regions))
        for recognizer_type in (KrRrnRecognizer, KrFrnRecognizer, KrPassportRecognizer,
                                KrDriverLicenseRecognizer, KrBrnRecognizer):
            registry.add_recognizer(recognizer_type(supported_language=language))
            additions.append({"name": recognizer_type.__name__, "language": language})
    engine = AnalyzerEngine(registry=registry, nlp_engine=nlp_engine, supported_languages=["en", "ko"])
    return engine, additions


def _parameter_count(model):
    if model is None:
        return 0
    seen, elements = set(), 0
    for component in model.walk():
        for name in component.param_names:
            if component.has_param(name):
                array = component.get_param(name)
                if id(array) not in seen:
                    seen.add(id(array))
                    elements += int(array.size)
    return elements


def engine_metadata(engine):
    models = []
    for entry in engine.nlp_engine.models:
        lang, name = entry["lang_code"], entry["model_name"]
        nlp = engine.nlp_engine.nlp[lang]
        package = __import__(name)
        directory = Path(package.__file__).parent
        model_hash = hashlib.sha256()
        artifact_bytes = 0
        for path in sorted(directory.rglob("*")):
            if path.is_file() and "__pycache__" not in path.parts:
                artifact_bytes += path.stat().st_size
                model_hash.update(str(path.relative_to(directory)).encode())
                model_hash.update(bytes.fromhex(file_hash(path)))
        models.append({"language": lang, "name": name,
                       "version": importlib.metadata.version(name),
                       "artifact_sha256": model_hash.hexdigest(), "installed_artifact_bytes": artifact_bytes,
                       "pipeline_components": nlp.pipe_names,
                       "native_ner_labels": list(nlp.get_pipe("ner").labels),
                       "pipeline_parameter_elements": sum(_parameter_count(pipe.model) for _, pipe in nlp.pipeline if hasattr(pipe, "model")),
                       "static_vector_elements": int(nlp.vocab.vectors.data.size),
                       "tokenizer_class": type(nlp.tokenizer).__module__ + "." + type(nlp.tokenizer).__name__})
    recognizers = []
    for r in engine.registry.recognizers:
        row = {"name": r.name, "class": type(r).__name__, "language": r.supported_language,
               "entities": r.supported_entities, "source_sha256": file_hash(inspect.getfile(type(r)))}
        if hasattr(r, "supported_regions"):
            row["phone_regions"] = list(r.supported_regions)
        recognizers.append(row)
    return {"models": models, "recognizers": recognizers,
            "nlp_ner_configuration": engine.nlp_engine.ner_model_configuration.model_dump(mode="json", exclude_none=True)}


def language_passes(configuration, source_language):
    if configuration == "en-default":
        return ["en"]
    if source_language == "mixed":
        return ["en", "ko"]
    if source_language not in {"en", "ko"}:
        raise ValueError("multilingual configuration requires en/ko/mixed language metadata")
    return [source_language]


def predict(engine, text, languages, threshold):
    raw, input_tokens = {}, 0
    for language in languages:
        artifacts = engine.nlp_engine.process_text(text, language)
        input_tokens += len(artifacts.tokens)
        results = engine.analyze(text=text, language=language, nlp_artifacts=artifacts,
                                 score_threshold=threshold, return_decision_process=False)
        for r in results:
            key = (r.start, r.end, r.entity_type)
            name = (r.recognition_metadata or {}).get("recognizer_name", "unknown")
            if key not in raw:
                raw[key] = {"start": r.start, "end": r.end, "type": r.entity_type,
                            "score": float(r.score), "recognizers": set(), "languages": set()}
            row = raw[key]
            row["score"] = max(row["score"], float(r.score))
            row["recognizers"].add(name)
            row["languages"].add(language)
    raw_rows, canonical = [], {}
    for key in sorted(raw):
        row = raw[key]
        row["recognizers"], row["languages"] = sorted(row["recognizers"]), sorted(row["languages"])
        raw_rows.append(row)
        mapped_type = ENTITY_MAPPING.get(row["type"])
        if mapped_type:
            mapped_key = (row["start"], row["end"], mapped_type)
            candidate = {"start": row["start"], "end": row["end"], "type": mapped_type, "score": row["score"]}
            if mapped_key not in canonical or canonical[mapped_key]["score"] < candidate["score"]:
                canonical[mapped_key] = candidate
    return [canonical[key] for key in sorted(canonical)], raw_rows, input_tokens


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--input", type=Path, default=Path("runs/pii/diverse-v1/test.jsonl"))
    parser.add_argument("--output-prefix", type=Path, required=True)
    parser.add_argument("--configuration", choices=["en-default", "en-ko"], default="en-default")
    parser.add_argument("--score-threshold", type=float, default=0.0)
    parser.add_argument("--threads", type=int, default=1)
    args = parser.parse_args(argv)
    if not 0 <= args.score_threshold <= 1 or args.threads < 1:
        parser.error("invalid score threshold or CPU thread count")
    for name in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"):
        os.environ[name] = str(args.threads)
    args.output_prefix.parent.mkdir(parents=True, exist_ok=True)
    predictions = Path(str(args.output_prefix) + ".predictions.jsonl")
    metadata_path = Path(str(args.output_prefix) + ".metadata.json")
    if predictions.exists() or metadata_path.exists():
        parser.error("refusing to overwrite benchmark artifacts")
    model_started = time.perf_counter()
    engine, additions = build_engine(args.configuration)
    model_load_seconds = time.perf_counter() - model_started
    metadata = {"backend": "presidio-spacy", "configuration": args.configuration,
                "supported_languages": engine.supported_languages,
                "language_selection": "always en (Korean/mixed rows outside advertised English NER support)" if args.configuration == "en-default" else "row.language metadata; mixed unions en and ko passes",
                "input_sha256": file_hash(args.input), "coordinate": "character",
                "score_threshold": args.score_threshold, "threshold_policy": "default 0.0; not tuned on test",
                "score_kind": "mixed NER default scores and recognizer heuristic scores; uncalibrated",
                "entity_mapping": ENTITY_MAPPING,
                "mapping_caveats": [
                    "LOCATION maps to ADDRESS as a granularity proxy; partial city/street ranges are not expanded.",
                    "KR_BRN is a business identifier; mapped to the broad IDENTIFIER category explicitly.",
                    "Financial account, credit-card, date and other unsupported labels stay in raw_model_spans but are excluded from five-type spans.",
                    "Synthetic benchmark IDs are not verified real identity numbers. Current KR_RRN validation returns None on an old-checksum mismatch, permitting post-2020 formats.",
                ],
                "official_recognizers_explicitly_enabled": additions,
                "custom_regex_or_context_added": False, "ganglion_interpreter_applied": False,
                "gold_accessed_by_detector": False, "device": "cpu", "cpu_threads": args.threads,
                "input_token_unit": "spaCy NLP tokens, summed over selected language passes; not LLM tokens",
                "python": platform.python_version(), "model_load_seconds": model_load_seconds,
                "packages": {name: version for name, version in sorted((d.metadata["Name"], d.version) for d in importlib.metadata.distributions())},
                "sources": OFFICIAL_SOURCES, **engine_metadata(engine)}
    latencies, language_counts, passes_counts, ignored_counts = [], Counter(), Counter(), Counter()
    total_tokens = 0
    started = time.perf_counter()
    temporary = predictions.with_suffix(predictions.suffix + ".partial")
    try:
        with args.input.open(encoding="utf-8") as source, temporary.open("x", encoding="utf-8") as target:
            for index, line in enumerate(source):
                source_row = json.loads(line)
                # Only these fields reach the detector; gold/family/template fields do not.
                sample_id, text, language = source_row["id"], source_row["text"], source_row.get("language")
                del source_row
                passes = language_passes(args.configuration, language)
                row_started = time.perf_counter()
                spans, raw, tokens = predict(engine, text, passes, args.score_threshold)
                elapsed_ms = (time.perf_counter() - row_started) * 1000
                result = {"id": sample_id, "spans": spans, "raw_model_spans": raw,
                          "language_passes": passes, "latency_ms": elapsed_ms,
                          "input_tokens": tokens, "output_tokens": 0}
                target.write(json.dumps(result, ensure_ascii=False) + "\n")
                latencies.append(elapsed_ms)
                language_counts[language] += 1
                passes_counts.update(passes)
                total_tokens += tokens
                ignored_counts.update(r["type"] for r in raw if r["type"] not in ENTITY_MAPPING)
                if (index + 1) % 250 == 0:
                    print(json.dumps({"rows": index + 1, "seconds": round(time.perf_counter() - started, 2)}), flush=True)
        os.link(temporary, predictions)
    finally:
        temporary.unlink(missing_ok=True)
    metadata.update(rows=len(latencies), input_tokens=total_tokens, output_tokens=0,
                    source_language_counts=dict(language_counts), language_pass_counts=dict(passes_counts),
                    ignored_native_entity_counts=dict(sorted(ignored_counts.items())),
                    elapsed_seconds=time.perf_counter() - started,
                    latency_ms={"median": statistics.median(latencies), "mean": statistics.mean(latencies), "p95": sorted(latencies)[max(0, int(len(latencies) * 0.95) - 1)]},
                    peak_process_rss_mb=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024,
                    predictions_sha256=file_hash(predictions))
    with metadata_path.open("x", encoding="utf-8") as target:
        json.dump(metadata, target, ensure_ascii=False, indent=2)
    print(json.dumps({"status": "complete", "rows": len(latencies), "predictions": str(predictions), "metadata": str(metadata_path)}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
