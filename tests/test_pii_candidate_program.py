"""Candidate backend integration uses the same contracts without loading a GPU."""
from __future__ import annotations

import base64
import copy
import importlib.util
import json
import re
import sys
import time
from pathlib import Path
from types import ModuleType

import pytest

from ganglion.console.api import ConsoleAPI
from ganglion.domains.pii.types import Span, interpret
from ganglion.lm.registry import Registry, RULES_SPEC
from ganglion.programs.specs import builtins, candidate_preprocessing, fingerprint, validate


def call(api, method, path, body=None):
    status, result = api.handle(method, '/api/v2/' + path, {}, body)
    assert 200 <= status < 300, (status, result)
    return result


def upload(api, text):
    return call(api, 'POST', 'uploads', {'data': base64.b64encode(text.encode()).decode()})['upload_id']


def wait_job(api, job_id):
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        job = call(api, 'GET', 'jobs/' + job_id)
        if job['status'] not in {'queued', 'running', 'cancelling'}:
            return job
        time.sleep(.01)
    pytest.fail('candidate job did not finish')


@pytest.fixture
def candidate_api(monkeypatch, tmp_path):
    source = tmp_path / 'source-checkpoint'
    checkpoint = tmp_path / 'candidate-checkpoint'
    source.mkdir(); checkpoint.mkdir()
    (source / 'model.json').write_text(json.dumps({'base_model': 'Qwen/Qwen3.5-0.8B'}))
    (source / 'heads.safetensors').write_bytes(b'fake-frozen-source-heads')
    config = candidate_preprocessing(builtins()['pii-qwen-candidates'])
    metadata = {'base_model': 'Qwen/Qwen3.5-0.8B', 'max_tokens': 128, 'source_checkpoint': str(source), 'candidate_config': config,
                'ready': True, 'training_complete': True}
    (checkpoint / 'model.json').write_text(json.dumps(metadata))
    (checkpoint / 'candidate_heads.safetensors').write_bytes(b'fake-candidate-heads')
    (checkpoint / 'vocab.json').write_text('{}')
    monkeypatch.setenv('GANGLION_PII_CANDIDATE_CHECKPOINT', str(checkpoint))
    monkeypatch.delenv('GANGLION_PII_CANDIDATE_FALLBACK_CHECKPOINT', raising=False)
    calls = []

    class FakeCandidateDetector:
        backend = 'qwen_candidates'
        score_kind = 'uncalibrated_probability'

        def __init__(self, path, *, candidate_config=None, fallback_checkpoint=None):
            self.metadata = json.loads((Path(path) / 'model.json').read_text())
            if candidate_config != self.metadata['candidate_config']:
                raise ValueError('candidate config mismatch')
            self.config = candidate_config
            calls.append({'path': Path(path), 'config': dict(candidate_config), 'fallback': fallback_checkpoint})

        def token_count(self, text):
            return len(text)

        def detect(self, text):
            self.text = text
            spans = []
            for match in re.finditer(r'[\w.]+@[\w.]+\.[a-z]+', text):
                # Return overlapping alternatives to exercise interpreter dispatch.
                spans += [Span(max(0, match.start()-1), match.end(), 'EMAIL', .7), Span(match.start(), match.end(), 'EMAIL', .9)]
            self.last_spans = spans
            return spans

        def interpret(self, text, raw):
            final, changes = interpret(text, raw[1::2])
            return final, [{'rule': 'candidate-selection-test', 'selected': len(final)}] + changes

        def get_last_diagnostics(self):
            overflow = self.text.count('@') > self.config['max_candidates']
            return {'candidates': len(self.last_spans), 'proposed_count': len(self.last_spans), 'overflow': overflow,
                    'fallback_count': int(overflow), 'context_characters': len(self.text), 'source_characters': len(self.text),
                    'encoded_tokens': len(self.text), 'output_candidates': len(self.last_spans),
                    'candidate_sources': {'email-shape': len(self.last_spans), self.text: 8, 'private-value': 3},
                    'value': self.text, 'left': self.text, 'right': self.text, 'candidate_ir': {'text': self.text}}

    module = ModuleType('ganglion.domains.pii.candidate_model')
    module.CandidateDetector = FakeCandidateDetector
    monkeypatch.setitem(sys.modules, module.__name__, module)
    api = ConsoleAPI(base_dir=tmp_path / 'runs' / 'traces', registry=Registry([RULES_SPEC]))
    yield api, calls, checkpoint, source
    api.close()


def test_candidate_program_is_optional_and_preserves_existing_defaults(candidate_api):
    api, calls, checkpoint, source = candidate_api
    specs = {row['id']: row for row in call(api, 'GET', 'specs')['specs']}
    assert {'pii-rules', 'pii-qwen', 'pii-qwen-candidates', 'tool-planner'} <= specs.keys()
    assert specs['pii-qwen']['model']['backend'] == 'qwen_native'
    spec = specs['pii-qwen-candidates']
    assert spec['available'] and spec['contract'] == {'output': 'TextEditPlan', 'coordinate': 'utf8-byte'}
    assert spec['preprocessor']['config'] == specs['pii-qwen']['preprocessor']['config']
    assert spec['preprocessor']['candidate_graph']['version'] == 1
    assert not calls, 'read-only discovery must not instantiate or load models'
    (checkpoint / 'vocab.json').unlink()
    assert not call(api, 'GET', 'specs/pii-qwen-candidates')['available']
    assert call(api, 'GET', 'specs/pii-rules')['available']


@pytest.mark.parametrize('key,value', [('context_chars', True), ('context_chars', -1), ('context_chars', 4097),
                                      ('max_candidates', 0), ('max_candidates', '256'), ('max_candidates', 10001),
                                      ('max_candidate_chars', False), ('max_candidate_chars', 0), ('max_candidate_chars', 4097)])
def test_candidate_budgets_are_strict_and_bounded(key, value):
    spec = copy.deepcopy(builtins()['pii-qwen-candidates'])
    spec['preprocessor']['candidate_graph']['config'][key] = value
    with pytest.raises(ValueError):
        validate(spec)


@pytest.mark.parametrize('change', [{'version': True}, {'version': 2}, {'adapter': 'python'}, {'config': {}}, {'private_value': 'secret'}])
def test_candidate_descriptor_rejects_unknown_or_incomplete_contract(change):
    spec = copy.deepcopy(builtins()['pii-qwen-candidates'])
    spec['preprocessor']['candidate_graph'].update(change)
    with pytest.raises(ValueError):
        validate(spec)


def test_candidate_graph_is_required_and_cannot_be_attached_to_other_backends():
    spec = copy.deepcopy(builtins()['pii-qwen-candidates'])
    del spec['preprocessor']['candidate_graph']
    with pytest.raises(ValueError): validate(spec)
    spec = copy.deepcopy(builtins()['pii-qwen-candidates'])
    spec['model']['backend'] = 'rules'
    with pytest.raises(ValueError): validate(spec)


def test_candidate_ir_budgets_change_application_fingerprint():
    spec = copy.deepcopy(builtins()['pii-qwen-candidates'])
    before = fingerprint(validate(spec))
    spec['preprocessor']['candidate_graph']['config']['context_chars'] = 24
    assert fingerprint(validate(spec)) != before


def test_candidate_inputs_cannot_override_pinned_ir_budgets(candidate_api):
    api, *_ = candidate_api
    document = upload(api, '메일: private@example.com')
    for override in [{'context_chars': 0}, {'max_candidates': 1}, {'candidate_config': {}}, {'window_chars': 8192}]:
        status, _ = api.handle('POST', '/api/v2/jobs', {}, {'spec_id': 'pii-qwen-candidates', 'inputs': {'document': document, 'execute': False, **override}})
        assert status == 400
        assert document in api._programs.uploads


@pytest.mark.parametrize('flags', [{'ready':False}, {'training_complete':False}, {'ready':'true'}, {'training_complete':1}, {'ready':None}])
def test_candidate_training_checkpoints_remain_unavailable_and_do_not_consume_input(candidate_api, flags):
    api, _, checkpoint, _ = candidate_api
    metadata = json.loads((checkpoint/'model.json').read_text())
    metadata.update(flags)
    (checkpoint/'model.json').write_text(json.dumps(metadata))
    spec=call(api,'GET','specs/pii-qwen-candidates')
    assert not spec['available']
    document=upload(api,'메일: private@example.com')
    status,_=api.handle('POST','/api/v2/jobs',{}, {'spec_id':spec['id'],'inputs':{'document':document,'execute':False}})
    assert status==400 and document in api._programs.uploads


def test_incomplete_candidate_metadata_is_read_only_unavailable(candidate_api):
    api, _, checkpoint, _ = candidate_api
    (checkpoint/'model.json').write_text('{partial')
    assert not call(api,'GET','specs/pii-qwen-candidates')['available']


def test_candidate_plan_maps_global_utf8_and_keeps_diagnostics_private(candidate_api):
    api, calls, checkpoint, source = candidate_api
    text = '가나다라 설명 문장입니다.\n' * 15 + '메일: private@example.com\n' + '개인정보 없음.\n' * 12
    doc = upload(api, text)
    started = call(api, 'POST', 'jobs', {'spec_id': 'pii-qwen-candidates', 'inputs': {'document': doc, 'execute': False}})
    job = wait_job(api, started['job_id'])
    assert job['status'] == 'complete', job
    result = job['result']
    assert result['backend'] == 'qwen_candidates' and result['output_tokens'] == 0
    assert result['preprocessor']['candidate_graph'] == builtins()['pii-qwen-candidates']['preprocessor']['candidate_graph']
    assert calls[0] == {'path': checkpoint, 'config': candidate_preprocessing(builtins()['pii-qwen-candidates']), 'fallback': None}
    artifacts = api._programs.root / 'jobs' / job['job_id'] / 'artifacts'
    plan = list(map(json.loads, (artifacts / 'plan.jsonl').read_text().splitlines()))
    start = len(text[:text.index('private@example.com')].encode())
    assert plan == [{'operation': 'replace', 'source_span': [start, start + len('private@example.com')], 'type': 'EMAIL'}]
    trace = list(map(json.loads, (artifacts / 'trace.jsonl').read_text().splitlines()))
    assert len(trace) > 1 and all(row['characters'] <= 128 for row in trace)
    assert any(len(row['raw_spans']) == 2 and len(row['final_spans']) == 1 for row in trace)
    assert any(change['rule'] == 'candidate-selection-test' for row in trace for change in row['corrections'])
    for path in artifacts.glob('*'):
        assert 'private@example.com' not in path.read_text(), path
        assert 'candidate_ir' not in path.read_text(), path
    diagnostics = result['candidate_diagnostics']
    assert diagnostics['candidate_sources'].keys() <= {'email-shape'}
    assert diagnostics['budget'] == calls[0]['config']
    assert diagnostics['overflow_units'] == diagnostics['fallback_count'] == 0
    assert all(set(row['candidate_diagnostics']) <= {'candidates','proposed_count','overflow','fallback_count','context_characters','source_characters','encoded_tokens','output_candidates','candidate_sources'} for row in trace)
    assert job['model_fingerprint'] == result['model_fingerprint']
    assert json.loads((artifacts / 'manifest.json').read_text())['model_fingerprint'] == result['model_fingerprint']
    assert not list((api._programs.root / 'private').glob('*'))
    # Same config uses one loaded runtime, with a new input document.
    second = call(api, 'POST', 'jobs', {'spec_id':'pii-qwen-candidates','inputs':{'document':upload(api,'메일: another@example.com'),'execute':False}})
    assert wait_job(api, second['job_id'])['status'] == 'complete'
    assert len(calls) == 1


def test_candidate_fingerprint_changes_with_source_weights(candidate_api):
    api, _, checkpoint, source = candidate_api
    call(api, 'GET', 'specs')
    before = api._programs._candidate_model_fingerprint()
    (source / 'heads.safetensors').write_bytes(b'new source weights')
    assert api._programs._candidate_model_fingerprint() == before, 'loaded runtime identity stays pinned'
    api._programs.candidate_model_stamp = None
    assert api._programs._candidate_model_fingerprint() != before


def test_candidate_input_tokens_report_compact_encoder_not_raw_window_budget(candidate_api, monkeypatch):
    api, *_ = candidate_api
    detector_class=sys.modules['ganglion.domains.pii.candidate_model'].CandidateDetector
    original=detector_class.get_last_diagnostics
    def compact_diagnostics(self):
        diagnostics=original(self)
        diagnostics['encoded_tokens']=7
        return diagnostics
    monkeypatch.setattr(detector_class,'get_last_diagnostics',compact_diagnostics)
    text='개인정보가 없는 원문을 모델 토큰 예산에 맞추어 분할합니다.\n'*15
    started=call(api,'POST','jobs',{'spec_id':'pii-qwen-candidates','inputs':{'document':upload(api,text),'execute':False}})
    job=wait_job(api,started['job_id'])
    assert job['status']=='complete',job
    result=job['result']
    assert result['input_tokens']==result['candidate_diagnostics']['encoded_tokens']==7*result['units']
    assert result['source_input_tokens']>result['input_tokens']


def test_candidate_config_mismatch_fails_without_publishing_or_retaining_input(candidate_api):
    api, *_ = candidate_api
    spec = copy.deepcopy(builtins()['pii-qwen-candidates'])
    spec['id'] = 'different-candidate-config'
    spec['preprocessor']['candidate_graph']['config']['context_chars'] = 0
    call(api,'POST','specs',spec)
    started = call(api,'POST','jobs',{'spec_id':spec['id'],'inputs':{'document':upload(api,'메일: private@example.com'),'execute':False}})
    job=wait_job(api,started['job_id'])
    assert job['status']=='failed'
    assert not list((api._programs.root/'private').glob('*'))
    assert not (api._programs.root/'jobs'/job['job_id']/'artifacts').exists()


def test_candidate_overflow_fallback_is_visible_and_never_silent(candidate_api):
    api, _, checkpoint, _ = candidate_api
    config = candidate_preprocessing(builtins()['pii-qwen-candidates'])
    config['max_candidates'] = 1
    metadata = json.loads((checkpoint / 'model.json').read_text())
    metadata['candidate_config'] = config
    (checkpoint / 'model.json').write_text(json.dumps(metadata))
    spec = copy.deepcopy(builtins()['pii-qwen-candidates'])
    spec.update(id='candidate-overflow-test', executor=None)
    spec['preprocessor']['candidate_graph']['config'] = config
    call(api, 'POST', 'specs', spec)
    started = call(api, 'POST', 'jobs', {'spec_id':spec['id'], 'inputs':{'document':upload(api,'메일: first@example.com / second@example.com'),'execute':False}})
    job = wait_job(api, started['job_id'])
    assert job['status'] == 'complete', job
    diagnostics = job['result']['candidate_diagnostics']
    assert diagnostics['overflow_units'] == diagnostics['fallback_count'] == 1
    assert job['result']['edits'] == 2
    directory = api._programs.root / 'jobs' / job['job_id'] / 'artifacts'
    trace = json.loads((directory/'trace.jsonl').read_text())
    assert trace['candidate_diagnostics']['overflow']
    assert json.loads((directory/'manifest.json').read_text())['candidate_diagnostics']['fallback_count'] == 1


@pytest.mark.skipif(importlib.util.find_spec('nacl') is None, reason='install ganglion[pii]')
def test_candidate_execution_uses_existing_exact_recovery_contract(candidate_api):
    api, *_ = candidate_api
    keys = call(api, 'POST', 'keys', {})
    original = '한글 앞부분🙂\r\n메일: private@example.com\r\n끝부분'
    started = call(api, 'POST', 'jobs', {'spec_id':'pii-qwen-candidates','inputs':{'document':upload(api,original),'public_key':keys['public_key']}})
    job = wait_job(api, started['job_id'])
    assert job['status']=='complete',job
    directory=api._programs.root/'jobs'/job['job_id']/'artifacts'
    redacted=(directory/'document.txt').read_bytes()
    assert b'private@example.com' not in redacted
    def upload_bytes(data):
        return call(api,'POST','uploads',{'data':base64.b64encode(data).decode()})['upload_id']
    restored=call(api,'POST','restore',{'document':upload_bytes(redacted),'recovery':upload_bytes((directory/'recovery.bin').read_bytes()),'private_key':keys['private_key']})
    restored_path=api._programs.root/'jobs'/restored['job_id']/'artifacts'/'restored.txt'
    assert restored_path.read_bytes()==original.encode()
