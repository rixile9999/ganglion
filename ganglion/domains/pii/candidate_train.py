"""Train candidate judgments; only train/validation files are ever opened.

The existing validation-selected Qwen encoder/readout are frozen. Source values
and coordinates enter as candidate IR; exact candidate matches are positives,
including negative examples for incomplete boundaries. No test-driven rules.
"""
from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import random
import time

from .candidate_model import CandidateDetector, CLASSES, checkpoint_fingerprint, make_candidate_heads
from .candidates import Candidate, CandidateConfig
from .types import ENTITY_TYPES


def targets_for_candidates(candidates, gold):
    expected = {(s['start'], s['end']): CLASSES.index(s['type']) for s in gold}
    return [expected.get((c.start, c.end), len(ENTITY_TYPES)) for c in candidates]


def _metric(tp, fp, fn):
    return {'tp': tp, 'fp': fp, 'fn': fn,
            'precision': tp / (tp + fp) if tp + fp else (0.0 if fn else 1.0),
            'recall': tp / (tp + fn) if tp + fn else 1.0,
            'f1': 2 * tp / (2 * tp + fp + fn) if 2 * tp + fp + fn else 1.0}


def evaluate_cached(detector, records, rows, thresholds):
    import torch
    all_tensors = [torch.cat([record['tensors'][i] for record in records]).to(detector.device) for i in range(5)]
    probabilities = []
    with torch.inference_mode():
        for start in range(0, len(all_tensors[0]), 2048):
            probabilities.extend(detector.candidate_heads(*(tensor[start:start + 2048] for tensor in all_tensors)).softmax(-1).cpu().tolist())
    counts = {threshold: {'raw': [0, 0, 0], 'interpreted': [0, 0, 0]} for threshold in thresholds}
    position = 0
    from .types import Span
    for row, record in zip(rows, records):
        window_probabilities = probabilities[position:position + len(record['candidates'])]
        position += len(record['candidates'])
        gold = {(s['start'], s['end'], s['type']) for s in row['spans']}
        for threshold in thresholds:
            raw = []
            for candidate, distribution in zip(record['candidates'], window_probabilities):
                kind = max(range(len(CLASSES)), key=lambda i: distribution[i])
                if kind < len(ENTITY_TYPES) and distribution[kind] >= threshold:
                    raw.append(Span(candidate.start, candidate.end, CLASSES[kind], distribution[kind],
                                    'candidate:' + candidate.id, dict(zip(CLASSES, distribution))))
            final, _ = detector.interpret(row['text'], raw)
            for stage, spans in [('raw', raw), ('interpreted', final)]:
                predicted = {(s.start, s.end, s.kind) for s in spans}
                for index, entities in enumerate((gold & predicted, predicted - gold, gold - predicted)):
                    counts[threshold][stage][index] += len(entities)
    return {str(threshold): {stage: _metric(*totals) for stage, totals in stages.items()} for threshold, stages in counts.items()}


def cached_features(detector, rows, cache, source_hash, *, batch_size):
    import torch
    signature = hashlib.sha256(json.dumps({'data': source_hash, 'candidate_config': asdict(detector.config),
                                           'source_checkpoint': detector.encoder.path.as_posix(),
                                           'source_weights': checkpoint_fingerprint(detector.encoder.path),
                                           'vocab': detector.vocab,
                                           'implementation': [hashlib.sha256(Path(__file__).with_name(name).read_bytes()).hexdigest()
                                                              for name in ('candidates.py', 'candidate_model.py')],
                                           'torch': torch.__version__}, sort_keys=True).encode()).hexdigest()
    path = cache / (signature + '.pt')
    if path.exists():
        stored = torch.load(path, map_location='cpu', weights_only=True)
        records = []
        for entry in stored:
            candidates = tuple(Candidate(c['id'], c['start'], c['end'], '', '', '', None, (), ()) for c in entry['candidates'])
            records.append({'candidates': candidates, 'tensors': entry['tensors'], 'diagnostics': entry['diagnostics']})
        print(json.dumps({'cache_hit': str(path), 'documents': len(records)}), flush=True)
        return records
    records = []
    for start in range(0, len(rows), batch_size):
        texts = [row['text'] for row in rows[start:start + batch_size]]
        batch = detector.build_features(texts, batch_size)
        for record in batch:
            if record['fallback']:
                raise ValueError('training has proposal/context overflow; choose config only on train/validation')
            if not record.get('tensors'):
                import torch
                width = detector.encoder.metadata['head_width'] * 5
                record['tensors'] = (torch.empty(0, width), torch.empty(0, 16),
                                     torch.empty(0, detector.value_character_budget, dtype=torch.long),
                                     torch.empty(0, detector.context_character_budget, dtype=torch.long),
                                     torch.empty(0, detector.context_character_budget, dtype=torch.long))
        records.extend(batch)
        if start % 512 == 0 or start + batch_size >= len(rows):
            print(json.dumps({'cached_documents': len(records), 'split_documents': len(rows)}), flush=True)
    stored = [{'candidates': [{'id': c.id, 'start': c.start, 'end': c.end} for c in r['candidates']],
               'tensors': r['tensors'], 'diagnostics': r['diagnostics']} for r in records]
    cache.mkdir(parents=True, exist_ok=True, mode=0o700)
    torch.save(stored, path)
    path.chmod(0o600)
    return records


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data', type=Path, default=Path('runs/pii/diverse-v1'))
    parser.add_argument('--source-checkpoint', type=Path, default=Path('runs/pii/qwen-0.8b-v4-lora16'))
    parser.add_argument('--output', type=Path, default=Path('runs/pii/qwen-0.8b-candidates-v1'))
    parser.add_argument('--epochs', type=int, default=30)
    parser.add_argument('--patience', type=int, default=8)
    parser.add_argument('--batch-size', type=int, default=512)
    parser.add_argument('--encoder-batch-size', type=int, default=64)
    parser.add_argument('--learning-rate', type=float, default=3e-4)
    parser.add_argument('--seed', type=int, default=42)
    parser.add_argument('--context-chars', type=int, default=48)
    parser.add_argument('--max-candidates', type=int, default=256)
    args = parser.parse_args()
    if min(args.epochs, args.patience, args.batch_size, args.encoder_batch_size) < 1 or args.learning_rate <= 0:
        parser.error('positive training budgets required')
    if (args.output / 'model.json').exists():
        parser.error('use a fresh checkpoint directory to preserve previous experiments')
    import torch
    from safetensors.torch import save_file
    random.seed(args.seed)
    torch.manual_seed(args.seed)
    torch.set_num_threads(4)
    config = CandidateConfig(context_chars=args.context_chars, max_candidates=args.max_candidates)
    manifest = json.loads((args.data / 'manifest.json').read_text())
    rows, hashes = {}, {}
    for split in ('train', 'validation'):
        file = args.data / (split + '.jsonl')
        contents = file.read_bytes()
        hashes[split] = hashlib.sha256(contents).hexdigest()
        if hashes[split] != manifest['splits'][split]['sha256']:
            raise ValueError('training/validation corpus changed')
        rows[split] = [json.loads(line) for line in contents.decode().splitlines()]
    alphabet = sorted({c for row in rows['train'] for c in row['text']})
    vocab = {char: index + 2 for index, char in enumerate(alphabet)}
    device = 'cuda' if torch.cuda.is_available() else 'cpu'
    started = time.perf_counter()
    detector = CandidateDetector.for_training(args.source_checkpoint, vocab, config, device)
    for model in (detector.encoder.backbone, detector.encoder.heads):
        model.requires_grad_(False)
        model.eval()
    feature_records = {split: cached_features(detector, values, args.data / 'candidate-cache', hashes[split], batch_size=args.encoder_batch_size)
                       for split, values in rows.items()}
    cache_seconds = time.perf_counter() - started
    width = detector.encoder.metadata['head_width'] * 5
    detector.candidate_heads = make_candidate_heads(width, len(vocab) + 2).to(device)
    detector.threshold = .5
    tensors = [torch.cat([record['tensors'][i] for record in feature_records['train']]).to(device) for i in range(5)]
    targets = torch.tensor([label for row, record in zip(rows['train'], feature_records['train'])
                            for label in targets_for_candidates(record['candidates'], row['spans'])], dtype=torch.long, device=device)
    counts = Counter(targets.cpu().tolist())
    weights = torch.tensor([min(10.0, (len(targets) / max(1, counts[i])) ** .5) for i in range(len(CLASSES))], device=device)
    criterion = torch.nn.CrossEntropyLoss(weight=weights)
    optimizer = torch.optim.AdamW(detector.candidate_heads.parameters(), lr=args.learning_rate, weight_decay=.01)
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / 'vocab.json').write_text(json.dumps(vocab, ensure_ascii=False))
    metadata = {'contract': 'pii-candidate-judgments-v1', 'classes': list(CLASSES),
                'base_model': detector.encoder.metadata['base_model'],
                'base_model_revision': detector.encoder.metadata['base_model_revision'],
                'source_checkpoint': str(args.source_checkpoint), 'source_checkpoint_sha256': checkpoint_fingerprint(args.source_checkpoint),
                'candidate_config': asdict(config), 'context_character_budget': detector.context_character_budget,
                'max_tokens': detector.encoder.metadata['max_tokens'], 'pooled_size': width,
                'head_width': 256, 'head_dropout': .15,
                'head_parameters': sum(p.numel() for p in detector.candidate_heads.parameters()),
                'encoder_parameters': detector.encoder.metadata['backbone_parameters'] + detector.encoder.metadata.get('trainable_backbone_parameters', 0) + detector.encoder.metadata['head_parameters'],
                'encoder_frozen': True, 'training_splits_sha256': hashes,
                'test_sha256_expected': manifest['splits']['test']['sha256'], 'test_used_for_tuning': False,
                'input_representation': 'source-mapped context blocks + candidate feature tensors and character values',
                'generated_output_tokens': 0, 'threshold_grid': [.3, .5, .7], 'seed': args.seed,
                'training_args': {key: str(value) if isinstance(value, Path) else value for key, value in vars(args).items()},
                'candidate_label_counts': {CLASSES[key]: value for key, value in counts.items()},
                'cache_seconds': cache_seconds, 'torch': torch.__version__}
    best, last_improvement, history = -1.0, 0, []
    training_started = time.perf_counter()
    for epoch in range(1, args.epochs + 1):
        detector.candidate_heads.train()
        order = torch.randperm(len(targets), device=device)
        total_loss = 0.0
        for start in range(0, len(targets), args.batch_size):
            indices = order[start:start + args.batch_size]
            optimizer.zero_grad(set_to_none=True)
            logits = detector.candidate_heads(*(tensor[indices] for tensor in tensors))
            loss = criterion(logits, targets[indices])
            if not bool(torch.isfinite(loss)):
                raise ValueError('non-finite candidate training loss')
            loss.backward()
            torch.nn.utils.clip_grad_norm_(detector.candidate_heads.parameters(), 1.0)
            optimizer.step()
            total_loss += float(loss.detach()) * len(indices)
        detector.candidate_heads.eval()
        scores = evaluate_cached(detector, feature_records['validation'], rows['validation'], metadata['threshold_grid'])
        selected = max(scores, key=lambda key: (scores[key]['interpreted']['f1'], -abs(float(key) - .5)))
        score = scores[selected]['interpreted']['f1']
        record = {'epoch': epoch, 'loss': total_loss / len(targets), 'threshold': float(selected),
                  'validation': scores[selected], 'seconds': time.perf_counter() - training_started}
        history.append(record)
        print(json.dumps(record), flush=True)
        if score > best:
            best, last_improvement = score, epoch
            metadata.update(selected_epoch=epoch, threshold=float(selected), validation=scores[selected],
                            selection_rule='validation interpreted exact typed span F1; no test access')
            save_file({name: tensor.detach().cpu().contiguous() for name, tensor in detector.candidate_heads.state_dict().items()}, str(args.output / 'candidate_heads.safetensors'))
            (args.output / 'model.json').write_text(json.dumps(metadata, ensure_ascii=False, indent=2))
        if epoch - last_improvement >= args.patience:
            break
    (args.output / 'training.json').write_text(json.dumps({'history': history, 'completed_epochs': len(history),
                                                         'training_seconds': time.perf_counter() - training_started,
                                                         'best_validation_f1': best, 'test_opened': False}, indent=2))
    metadata.update(ready=True, training_complete=True)
    temporary = args.output / 'model.json.tmp'
    temporary.write_text(json.dumps(metadata, ensure_ascii=False, indent=2))
    temporary.replace(args.output / 'model.json')
    print(json.dumps({'checkpoint': str(args.output), 'selected_epoch': metadata['selected_epoch'],
                      'validation_f1': best, 'test_opened': False}), flush=True)


if __name__ == '__main__':
    main()
