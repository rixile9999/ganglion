"""Train typed and character-boundary heads, select only on validation gold.

The backbone remains frozen so Interpreter gains can be isolated. Cached
features allow actual hyperparameter comparisons without repeated inference.
Test labels are used once, after the best validation checkpoint is fixed.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import json
import os
from pathlib import Path
import random
import threading
import time

from .native import load_backbone, make_heads, NativeDetector, decode_spans
from .types import ENTITY_TYPES, LABELS, interpret


def aligned_targets(offsets, gold, boundary_classes=32):
    """Assign BIO/type and gold character trims; never round gold to tokens."""
    labels, types = [], []
    starts, ends = [-100] * len(offsets), [-100] * len(offsets)
    previous = None
    for start, end in offsets:
        candidates = [s for s in gold if start < s['end'] and end > s['start']] if end > start else []
        if len(candidates) > 1:
            raise ValueError('one token overlaps multiple gold entities; token BIO cannot represent this annotation')
        matching = candidates[0] if candidates else None
        identity = (matching['start'], matching['end'], matching['type']) if matching else None
        labels.append(LABELS.index(('I-' if identity == previous else 'B-') + matching['type']) if matching else 0)
        types.append(ENTITY_TYPES.index(matching['type']) if matching else len(ENTITY_TYPES))
        previous = identity
    for span in gold:
        covered = [i for i, (start, end) in enumerate(offsets) if start < span['end'] and end > span['start']]
        if not covered:
            raise ValueError('gold span has no tokenizer coverage')
        first, last = covered[0], covered[-1]
        left, right = span['start'] - offsets[first][0], offsets[last][1] - span['end']
        if not 0 <= left < boundary_classes or not 0 <= right < boundary_classes:
            raise ValueError('gold character trim exceeds boundary-head capacity')
        starts[first], ends[last] = left, right
    return labels, types, starts, ends


def _summary(totals):
    tp, fp, fn = totals
    precision = tp / (tp + fp) if tp + fp else (1.0 if not fn else 0.0)
    recall = tp / (tp + fn) if tp + fn else 1.0
    return {'tp': tp, 'fp': fp, 'fn': fn, 'precision': precision, 'recall': recall,
            'exact_span_f1': 2 * tp / (2 * tp + fp + fn) if 2 * tp + fp + fn else 1.0}


def score_predictions(rows, predictions, *, diagnostics=True):
    from ganglion.analyzer.domain_analysis import analyze_predictions
    if len(rows) != len(predictions):
        raise ValueError('every evaluation document requires captured predictions')
    totals = {'raw': [0, 0, 0], 'interpreted': [0, 0, 0]}
    per_type = {name: {kind: [0, 0, 0] for kind in ENTITY_TYPES} for name in totals}
    language = defaultdict(lambda: [0, 0, 0])
    histogram, attribution = Counter(), Counter()
    misses, failures, negative_fp = [], [], 0
    for row, raw in zip(rows, predictions):
        gold = {(s['start'], s['end'], s['type']) for s in row['spans']}
        final, _ = interpret(row['text'], raw)
        for name, spans in (('raw', raw), ('interpreted', final)):
            predicted = {(s.start, s.end, s.kind) for s in spans}
            tp, fp, fn = gold & predicted, predicted - gold, gold - predicted
            for index, values in enumerate((tp, fp, fn)):
                totals[name][index] += len(values)
                for kind in ENTITY_TYPES:
                    per_type[name][kind][index] += sum(s[2] == kind for s in values)
                if name == 'interpreted':
                    language[row.get('language', 'unspecified')][index] += len(values)
            if name == 'interpreted':
                if fn:
                    misses.append(row['id'])
                if fp and not gold:
                    negative_fp += 1
        if diagnostics:
            analyzed = analyze_predictions('pii-text', raw, final, row['spans'], document_length=len(row['text']))
            histogram.update(analyzed['histogram'])
            if analyzed['attribution']:
                attribution.update({key: analyzed['attribution'][key] for key in ('rescued_count', 'regressed_count')})
            if analyzed['status'] != 'pass':
                failures.append({'document_id': row['id'], 'template_id': row.get('template_id'),
                                 'histogram': analyzed['histogram'], 'failures': analyzed['failures']})
    result = {'documents': len(rows), 'missed_document_ids': misses, 'negative_documents_with_fp': negative_fp,
              'failure_histogram': dict(histogram), 'correction_attribution': dict(attribution),
              'per_language': {key: _summary(values) for key, values in language.items()}}
    for name, counts in totals.items():
        result[name] = {**_summary(counts), 'per_type': {kind: _summary(values) for kind, values in per_type[name].items()}}
    if diagnostics:
        result['document_failures'] = failures
    return result


def evaluate(detector, rows):
    started = time.perf_counter()
    result = score_predictions(rows, [detector.detect(row['text']) for row in rows])
    result['latency_ms'] = (time.perf_counter() - started) * 1000
    return result


def cache_features(backbone, tokenizer, rows, device, boundary_classes, cache_path, batch_size=64):
    import torch
    signature = hashlib.sha256(json.dumps({'rows': rows, 'revision': backbone.ganglion_revision,
                                           'config': backbone.config.to_dict(),
                                           'model_class': type(backbone).__name__, 'tokenizer_class': type(tokenizer).__name__,
                                           'tokenizer_revision': tokenizer.init_kwargs.get('revision'),
                                           'dtype': str(next(backbone.parameters()).dtype),
                                           'torch_version': torch.__version__, 'device_type': device.split(':')[0],
                                           'boundary_classes': boundary_classes}, sort_keys=True, default=str).encode()).hexdigest()
    cached = cache_path / (signature + '.pt') if cache_path else None
    if cached and cached.exists():
        # Only files produced in the explicitly selected local cache are read.
        features = torch.load(cached, map_location='cpu', weights_only=True)
        print(json.dumps({'cache_hit': str(cached), 'documents': len(rows)}), flush=True)
        return features
    features, padding_side = [], tokenizer.padding_side
    tokenizer.padding_side = 'right'
    if tokenizer.pad_token_id is None:
        tokenizer.pad_token = tokenizer.eos_token
    for start in range(0, len(rows), batch_size):
        batch = rows[start:start + batch_size]
        encoded = tokenizer([row['text'] for row in batch], return_tensors='pt', return_offsets_mapping=True,
                            add_special_tokens=False, padding=True)
        all_offsets = encoded.pop('offset_mapping').tolist()
        lengths = encoded['attention_mask'].sum(-1).tolist()
        with torch.inference_mode():
            hidden = backbone(**{key: value.to(device) for key, value in encoded.items()}, use_cache=False).last_hidden_state.cpu()
        for index, (row, n) in enumerate(zip(batch, lengths)):
            offsets = all_offsets[index][:n]
            targets = aligned_targets(offsets, row['spans'], boundary_classes)
            features.append({'hidden': hidden[index, :n].clone(), 'offsets': offsets, 'targets': targets})
        if start // 512 != (start + len(batch)) // 512 or start + len(batch) == len(rows):
            print(json.dumps({'cached_documents': start + len(batch), 'split_documents': len(rows)}), flush=True)
    tokenizer.padding_side = padding_side
    if cached:
        cache_path.mkdir(parents=True, exist_ok=True, mode=0o700)
        torch.save(features, cached)
        cached.chmod(0o600)
    return features


def batch_loss(outputs, expected, criterion_bio=None, criterion=None, boundary_weight=0.5):
    """Negative-only batches have no boundary targets and contribute zero there."""
    import torch
    criterion = criterion or torch.nn.CrossEntropyLoss(ignore_index=-100)
    criterion_bio = criterion_bio or criterion
    loss = criterion_bio(outputs[0].flatten(0, 1), expected[0].flatten())
    loss += criterion(outputs[1].flatten(0, 1), expected[1].flatten())
    for value, target in zip(outputs[2:], expected[2:]):
        if bool((target != -100).any()):
            loss += boundary_weight * criterion(value.flatten(0, 1), target.flatten())
    return loss


def pack_features(features, device):
    import torch
    length = max(len(row['hidden']) for row in features)
    width = features[0]['hidden'].shape[-1]
    hidden = torch.zeros(len(features), length, width, dtype=features[0]['hidden'].dtype, device=device)
    mask = torch.zeros(len(features), length, dtype=torch.bool, device=device)
    targets = [torch.full((len(features), length), -100, device=device, dtype=torch.long) for _ in range(4)]
    for index, row in enumerate(features):
        n = len(row['hidden'])
        hidden[index, :n] = row['hidden'].to(device)
        mask[index, :n] = True
        for tensor, values in zip(targets, row['targets']):
            tensor[index, :n] = torch.tensor(values, device=device)
    return hidden, mask, targets


def predict_cached(heads, packed, features, metadata, batch_size=128):
    import torch
    hidden, mask, _targets = packed
    predictions = []
    heads.eval()
    with torch.inference_mode():
        for start in range(0, len(features), batch_size):
            outputs = [value.cpu() for value in heads(hidden[start:start + batch_size], mask[start:start + batch_size])]
            for index, row in enumerate(features[start:start + batch_size]):
                n = len(row['offsets'])
                predictions.append(decode_spans(row['offsets'], tuple(value[index, :n] for value in outputs),
                                                temperature=metadata['temperature'], decode_mode=metadata['decode_mode'],
                                                joint_type_weight=metadata['joint_type_weight']))
    return predictions


def pack_tokens(tokenizer, rows, device, boundary_classes):
    import torch
    padding_side = tokenizer.padding_side
    tokenizer.padding_side = 'right'
    encoded = tokenizer([row['text'] for row in rows], return_tensors='pt', return_offsets_mapping=True,
                        add_special_tokens=False, padding=True)
    tokenizer.padding_side = padding_side
    offsets = encoded.pop('offset_mapping').tolist()
    lengths = encoded['attention_mask'].sum(-1).tolist()
    targets = [torch.full_like(encoded['input_ids'], -100) for _ in range(4)]
    features = []
    for index, (row, n) in enumerate(zip(rows, lengths)):
        valid_offsets = offsets[index][:n]
        for tensor, values in zip(targets, aligned_targets(valid_offsets, row['spans'], boundary_classes)):
            tensor[index, :n] = torch.tensor(values)
        features.append({'offsets': valid_offsets})
    return {key: value.to(device) for key, value in encoded.items()}, [value.to(device) for value in targets], features


def predict_tokens(backbone, heads, packed, metadata, batch_size=64):
    import torch
    encoded, _targets, features = packed
    predictions = []
    backbone.eval()
    heads.eval()
    with torch.inference_mode():
        for start in range(0, len(features), batch_size):
            batch = {key: value[start:start + batch_size] for key, value in encoded.items()}
            length = int(batch['attention_mask'].sum(1).max())
            batch = {key: value[:, :length] for key, value in batch.items()}
            hidden = backbone(**batch, use_cache=False).last_hidden_state
            outputs = [value.cpu() for value in heads(hidden, batch['attention_mask'])]
            for index, row in enumerate(features[start:start + batch_size]):
                n = len(row['offsets'])
                predictions.append(decode_spans(row['offsets'], tuple(value[index, :n] for value in outputs),
                                                temperature=metadata['temperature'], decode_mode=metadata['decode_mode'],
                                                joint_type_weight=metadata['joint_type_weight']))
    return predictions


def evaluate_checkpoint(checkpoint, data, *, device='cuda', batch_size=64, history=None, completed_epochs=None, cap_reason=None):
    """Evaluate a frozen saved checkpoint; this function has no optimizer path."""
    import torch
    detector = NativeDetector(checkpoint, device)
    metadata = detector.metadata
    if completed_epochs is not None:
        metadata.update(completed_epochs=completed_epochs, training_stop_reason=cap_reason)
        (Path(checkpoint) / 'model.json').write_text(json.dumps(metadata, indent=2))
    report = {'device': device, 'gpu': torch.cuda.get_device_name() if device.startswith('cuda') else None,
              'output_tokens': 0, 'synthetic_only': metadata.get('synthetic_only'),
              'selected_epoch': metadata['best_epoch'], 'checkpoint_selected_on': 'validation',
              'completed_epochs': completed_epochs, 'training_stop_reason': cap_reason,
              'history': history or [], 'splits': {}}
    from .rules import RulesDetector
    for split in ('validation', 'test'):
        rows = [json.loads(line) for line in (Path(data) / f'{split}.jsonl').read_text().splitlines()]
        packed = pack_tokens(detector.tokenizer, rows, device, metadata['boundary_classes'])
        started = time.perf_counter()
        predicted = predict_tokens(detector.backbone, detector.heads, packed, metadata, batch_size)
        result = score_predictions(rows, predicted)
        result.update(batched_runtime_latency_ms=(time.perf_counter() - started) * 1000, includes_backbone_latency=True)
        report['splits'][split] = {'native': result, 'rules': evaluate(RulesDetector(), rows)}
        if split == 'validation':
            # Compare actual single-document inference to the batched evaluator.
            count = min(32, len(rows))
            direct = [detector.detect(row['text']) for row in rows[:count]]
            coords = lambda spans: [(s.start, s.end, s.kind) for s in spans]
            report['runtime_parity'] = {'documents': count, 'mismatch_ids': [row['id'] for row, a, b in zip(rows[:count], predicted, direct) if coords(a) != coords(b)]}
    (Path(checkpoint) / 'evaluation.json').write_text(json.dumps(report, indent=2))
    print(json.dumps({'checkpoint': str(checkpoint), 'validation_f1': report['splits']['validation']['native']['interpreted']['exact_span_f1'],
                      'test_f1': report['splits']['test']['native']['interpreted']['exact_span_f1'], 'selected_epoch': metadata['best_epoch']}), flush=True)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--base-model', default='Qwen/Qwen3.5-0.8B')
    parser.add_argument('--epochs', type=int, default=80)
    parser.add_argument('--batch-size', type=int, default=128)
    parser.add_argument('--backbone-batch-size', type=int, default=64)
    parser.add_argument('--seed', type=int, default=42)
    parser.add_argument('--device', default='cuda')
    parser.add_argument('--head-width', type=int, default=256)
    parser.add_argument('--head-layers', type=int, default=2)
    parser.add_argument('--dropout', type=float, default=0.1)
    parser.add_argument('--o-weight', type=float, default=1.0)
    parser.add_argument('--learning-rate', type=float, default=0.0003)
    parser.add_argument('--boundary-classes', type=int, default=32)
    parser.add_argument('--boundary-loss-weight', type=float, default=0.5)
    parser.add_argument('--joint-type-weight', type=float, default=0.5)
    parser.add_argument('--feature-cache', type=Path)
    parser.add_argument('--eval-every', type=int, default=2)
    parser.add_argument('--patience', type=int, default=12)
    parser.add_argument('--min-epochs', type=int, default=20)
    parser.add_argument('--lora-rank', type=int, default=0)
    parser.add_argument('--lora-learning-rate', type=float, default=0.0001)
    parser.add_argument('--lora-dropout', type=float, default=0.05)
    parser.add_argument('--stop-file', type=Path, help='JSON {max_epochs, reason}; checked only after complete epochs')
    parser.add_argument('--evaluate-only', action='store_true', help='evaluate the saved output checkpoint without optimization')
    args = parser.parse_args(argv)
    if args.evaluate_only:
        evaluate_checkpoint(args.output, args.data, device=args.device, batch_size=args.backbone_batch_size)
        return 0
    if args.output.exists():
        parser.error('output must be a new checkpoint directory')
    if min(args.epochs, args.batch_size, args.backbone_batch_size, args.eval_every, args.boundary_classes) < 1 or args.o_weight <= 0:
        parser.error('invalid training settings')
    import torch
    from safetensors.torch import save_file
    torch.manual_seed(args.seed)
    random.seed(args.seed)
    torch.set_float32_matmul_precision('high')
    rows = {split: [json.loads(line) for line in (args.data / f'{split}.jsonl').read_text().splitlines()]
            for split in ('train', 'validation')}
    backbone, tokenizer = load_backbone(args.base_model, args.device)
    revision = backbone.ganglion_revision
    hidden_size = backbone.config.hidden_size
    backbone_parameters = sum(p.numel() for p in backbone.parameters())
    for parameter in backbone.parameters():
        parameter.requires_grad_(False)
    if args.lora_rank:
        from peft import get_peft_model, LoraConfig, TaskType
        backbone = get_peft_model(backbone, LoraConfig(r=args.lora_rank, lora_alpha=args.lora_rank * 2,
                                  lora_dropout=args.lora_dropout, target_modules='all-linear', task_type=TaskType.FEATURE_EXTRACTION))
        packed = {split: pack_tokens(tokenizer, data, args.device, args.boundary_classes) for split, data in rows.items()}
        features = {split: values[2] for split, values in packed.items()}
    else:
        features = {split: cache_features(backbone, tokenizer, data, args.device, args.boundary_classes, args.feature_cache, args.backbone_batch_size)
                    for split, data in rows.items()}
        packed = {split: pack_features(data, args.device) for split, data in features.items()}
    heads = make_heads(hidden_size, args.head_width, dropout=args.dropout,
                       layers=args.head_layers, boundary_classes=args.boundary_classes).to(args.device)
    groups = [{'params': list(heads.parameters()), 'lr': args.learning_rate}]
    if args.lora_rank:
        groups.append({'params': [p for p in backbone.parameters() if p.requires_grad], 'lr': args.lora_learning_rate})
    optimizer = torch.optim.AdamW(groups, weight_decay=0.01)
    weights = torch.ones(len(LABELS), device=args.device)
    weights[0] = args.o_weight
    criterion_bio = torch.nn.CrossEntropyLoss(weight=weights, ignore_index=-100)
    criterion = torch.nn.CrossEntropyLoss(ignore_index=-100)
    manifest = json.loads((args.data / 'manifest.json').read_text()) if (args.data / 'manifest.json').exists() else {}
    metadata = {'base_model': args.base_model, 'base_model_revision': revision,
                'labels': list(LABELS), 'head_width': args.head_width, 'head_layers': args.head_layers,
                'head_dropout': args.dropout, 'boundary_classes': args.boundary_classes, 'max_tokens': 128,
                'temperature': 1.0, 'decode_mode': 'bio-viterbi', 'joint_type_weight': args.joint_type_weight,
                'training': 'lora-character-boundary-heads' if args.lora_rank else 'frozen-backbone-character-boundary-heads', 'seed': args.seed,
                'lora_rank': args.lora_rank, 'lora_dropout': args.lora_dropout, 'lora_learning_rate': args.lora_learning_rate,
                'o_weight': args.o_weight, 'learning_rate': args.learning_rate,
                'planned_epochs': args.epochs, 'batch_size': args.batch_size,
                'boundary_loss_weight': args.boundary_loss_weight,
                'selection_split': 'validation', 'selection_metric': 'interpreted_exact_span_f1',
                'train_documents': len(rows['train']), 'synthetic_only': True,
                'dataset_manifest': manifest,
                'train_sha256': hashlib.sha256((args.data / 'train.jsonl').read_bytes()).hexdigest(),
                'validation_sha256': hashlib.sha256((args.data / 'validation.jsonl').read_bytes()).hexdigest(),
                'backbone_parameters': backbone_parameters,
                'trainable_backbone_parameters': sum(p.numel() for p in backbone.parameters() if p.requires_grad),
                'head_parameters': sum(p.numel() for p in heads.parameters())}
    args.output.mkdir(parents=True, mode=0o700)
    history, best, stale = [], -1.0, 0
    training_start = time.perf_counter()
    if args.lora_rank:
        encoded, targets, _ = packed['train']
        mask = encoded['attention_mask']
    else:
        hidden, mask, targets = packed['train']
    for epoch in range(args.epochs):
        epoch_start = time.perf_counter()
        heads.train()
        if args.lora_rank:
            backbone.train()
        indices = torch.randperm(len(features['train']), device=args.device)
        losses = []
        for start in range(0, len(indices), args.batch_size):
            batch = indices[start:start + args.batch_size]
            length = int(mask[batch].sum(1).max())
            optimizer.zero_grad(set_to_none=True)
            if args.lora_rank:
                values = {key: value[batch, :length] for key, value in encoded.items()}
                hidden_batch = backbone(**values, use_cache=False).last_hidden_state
            else:
                hidden_batch = hidden[batch, :length]
            outputs = heads(hidden_batch, mask[batch, :length])
            expected = [value[batch, :length].flatten() for value in targets]
            loss = batch_loss(outputs, expected, criterion_bio, criterion, args.boundary_loss_weight)
            loss.backward()
            torch.nn.utils.clip_grad_norm_([p for group in groups for p in group['params']], 1.0)
            optimizer.step()
            losses.append(float(loss.detach()))
        entry = {'epoch': epoch + 1, 'train_loss': sum(losses) / len(losses)}
        if (epoch + 1) % args.eval_every == 0 or epoch == args.epochs - 1:
            predicted = predict_tokens(backbone, heads, packed['validation'], metadata, args.backbone_batch_size) if args.lora_rank else predict_cached(heads, packed['validation'], features['validation'], metadata, args.batch_size)
            validation = score_predictions(rows['validation'], predicted, diagnostics=False)
            score = validation['interpreted']['exact_span_f1']
            entry.update(validation_f1=score, validation_counts=validation['interpreted'])
            if score > best:
                best, stale = score, 0
                metadata.update(best_epoch=epoch + 1, best_validation_f1=best)
                save_file({key: value.detach().cpu().contiguous() for key, value in heads.state_dict().items()}, str(args.output / 'heads.safetensors'))
                if args.lora_rank:
                    backbone.save_pretrained(args.output / 'adapter', safe_serialization=True)
                (args.output / 'model.json').write_text(json.dumps(metadata, indent=2))
            else:
                stale += 1
        entry['elapsed_seconds'] = time.perf_counter() - epoch_start
        history.append(entry)
        print(json.dumps(entry), flush=True)
        if args.stop_file and args.stop_file.exists():
            stop = json.loads(args.stop_file.read_text())
            if type(stop.get('max_epochs')) is not int or stop['max_epochs'] < 1:
                raise ValueError('stop file requires a positive integer max_epochs')
            if epoch + 1 >= stop['max_epochs']:
                metadata.update(training_stop_reason=stop.get('reason', 'explicit_epoch_cap'))
                break
        if epoch + 1 >= args.min_epochs and stale >= args.patience:
            break
    from safetensors.torch import load_file
    metadata['completed_epochs'] = len(history)
    (args.output / 'model.json').write_text(json.dumps(metadata, indent=2))
    heads.load_state_dict(load_file(str(args.output / 'heads.safetensors'), device=args.device))
    if args.lora_rank:
        from peft.utils.save_and_load import set_peft_model_state_dict
        set_peft_model_state_dict(backbone, load_file(str(args.output / 'adapter' / 'adapter_model.safetensors'), device=args.device))
        backbone.eval()
    heads.eval()
    # The test split is first read after checkpoint selection is complete.
    rows['test'] = [json.loads(line) for line in (args.data / 'test.jsonl').read_text().splitlines()]
    if args.lora_rank:
        packed['test'] = pack_tokens(tokenizer, rows['test'], args.device, args.boundary_classes)
        features['test'] = packed['test'][2]
    else:
        features['test'] = cache_features(backbone, tokenizer, rows['test'], args.device, args.boundary_classes, args.feature_cache, args.backbone_batch_size)
        packed['test'] = pack_features(features['test'], args.device)
    report = {'device': args.device, 'gpu': torch.cuda.get_device_name() if args.device.startswith('cuda') else None,
              'peak_gpu_memory_bytes': torch.cuda.max_memory_allocated() if args.device.startswith('cuda') else None,
              'output_tokens': 0, 'synthetic_only': True, 'selected_epoch': metadata['best_epoch'],
              'checkpoint_selected_on': 'validation', 'training_seconds': time.perf_counter() - training_start,
              'history': history, 'splits': {}}
    from .rules import RulesDetector
    for split in ('validation', 'test'):
        started = time.perf_counter()
        predictions = predict_tokens(backbone, heads, packed[split], metadata, args.backbone_batch_size) if args.lora_rank else predict_cached(heads, packed[split], features[split], metadata, args.batch_size)
        result = score_predictions(rows[split], predictions)
        result['cached_readout_latency_ms'] = (time.perf_counter() - started) * 1000
        result['includes_backbone_latency'] = bool(args.lora_rank)
        report['splits'][split] = {'native': result, 'rules': evaluate(RulesDetector(), rows[split])}
    (args.output / 'evaluation.json').write_text(json.dumps(report, indent=2))
    print(json.dumps({'checkpoint': str(args.output), 'validation_f1': report['splits']['validation']['native']['interpreted']['exact_span_f1'],
                      'test_f1': report['splits']['test']['native']['interpreted']['exact_span_f1'], 'selected_epoch': metadata['best_epoch']}), flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
