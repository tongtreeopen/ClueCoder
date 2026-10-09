"""Reporting must preserve denominators and reject invalid evidence."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from tracecoder.eval.report import render_markdown, summarize
from tests.test_eval_runner import SECRET, provider
from tests.test_eval_runner import source as source  # noqa: PLC0414 - pytest fixture re-export

ROOT = Path(__file__).resolve().parents[1]
IDS = ['csv_quotes', 'retry_backoff', 'ttl_cache', 'tree_walk', 'date_ranges', 'config_merge',
       'log_filter', 'dependency_order']


def valid_results():
    return [{'task_id': task, 'status': 'pass', 'gate_status': 'pass', 'docker_valid': True, 'reproducible': True,
                 'usage_unknown': False, 'calls': 2, 'tokens': 12, 'agent_revision': 'a' * 40, 'image_id': 'sha256:' + 'b' * 64,
                 'forbidden_paths': []} for task in IDS]


def test_report_keeps_all_scheduled_cases():
    summary = summarize(valid_results()[:2], 8)
    assert summary['scheduled'] == len(summary['results']) == 8
    assert summary['passed'] == summary['gate_completed'] == 2
    assert summary['failure_counts']['setup_error'] == 6
    assert summary['success_rate'] is None
    assert summarize(valid_results(), 8)['success_rate'] == 1.0


@pytest.mark.parametrize(('change',), [
    ({'docker_valid': False},), ({'usage_unknown': True},), ({'tokens': 0},),
    ({'reproducible': False},), ({'gate_status': 'gate_error', 'status': 'gate_error'},),
    ({'agent_revision': 'c' * 40},), ({'status': 'setup_error'},),
])
def test_report_has_no_rate_without_valid_gate_or_usage(change):
    results = valid_results()
    results[0].update(change)
    assert summarize(results, 8)['success_rate'] is None


def test_report_rejects_forbidden_pass_and_duplicate_cases():
    results = valid_results()
    results[0]['forbidden_paths'] = ['unexpected.py']
    summary = summarize(results, 8)
    assert summary['passed'] == 7 and summary['failure_counts']['agent_error'] == 1
    assert summary['success_rate'] == 7 / 8
    results[1]['task_id'] = results[0]['task_id']
    assert summarize(results, 8)['success_rate'] is None


def test_report_without_any_completed_gate_has_no_rate():
    results = valid_results()
    for result in results:
        result.update(status='agent_error', gate_status=None)
    assert summarize(results, 8)['success_rate'] is None


def test_cli_missing_credentials_reports_eight_without_requests(tmp_path):
    env = {key: value for key, value in os.environ.items()
           if not any(part in key.upper() for part in ('KEY', 'TOKEN', 'SECRET', 'PASSWORD', 'CREDENTIAL'))}
    completed = subprocess.run([sys.executable, '-m', 'tracecoder.eval', 'run', '--suite',
                                str(ROOT / 'benchmarks/tracecoder-baseline-v1/manifest.json'), '--output',
                                str(tmp_path / 'report'), '--model', 'test', '--base-url', 'http://127.0.0.1:1/v1'],
                               cwd=ROOT, env=env, capture_output=True, text=True, timeout=30, check=False)
    assert completed.returncode == 2, completed.stderr
    summary = json.loads((tmp_path / 'report/summary.json').read_text(encoding='utf-8'))
    assert [item['task_id'] for item in summary['results']] == IDS
    assert summary['success_rate'] is None and summary['gate_completed'] == 0
    assert summary['failure_counts'] == {'setup_error': 8}
    assert 'credential' in summary['preflight_error'].lower()
    assert summary['suite_sha256'] and summary['suite_id'] == 'tracecoder-baseline-v1'
    assert summary['configuration']['max_tokens'] == 100000
    assert '0%' not in render_markdown(summary)


def test_cli_rejects_changed_suite_before_worker(tmp_path):
    (tmp_path / 'suite').mkdir()
    manifest = tmp_path / 'suite/manifest.json'
    manifest.write_text('{"id":"bad","tasks":[]}', encoding='utf-8')
    completed = subprocess.run([sys.executable, '-m', 'tracecoder.eval', 'run', '--suite', str(manifest),
                                '--output', str(tmp_path / 'report'), '--model', 'test'], cwd=ROOT,
                               capture_output=True, text=True, timeout=30, check=False)
    assert completed.returncode == 2
    summary = json.loads((tmp_path / 'report/summary.json').read_text(encoding='utf-8'))
    assert len(summary['results']) == 8 and summary['success_rate'] is None


def test_cli_never_archives_url_credentials(tmp_path):
    completed = subprocess.run([sys.executable, '-m', 'tracecoder.eval', 'run', '--suite',
                                str(ROOT / 'benchmarks/tracecoder-baseline-v1/manifest.json'), '--output',
                                str(tmp_path / 'report'), '--model', 'test', '--base-url',
                                'http://user:password-is-private@127.0.0.1:1/v1?key=private-query'],
                               cwd=ROOT, capture_output=True, text=True, timeout=30, check=False)
    assert completed.returncode == 2
    saved = ''.join(path.read_text(encoding='utf-8') for path in (tmp_path / 'report').iterdir())
    assert 'password-is-private' not in saved and 'private-query' not in saved
    assert '[INVALID_URL]' in saved


def test_cli_runs_all_cases_with_real_docker_and_scripted_provider(docker_ready, source, tmp_path):
    env = {**os.environ, 'TRACECODER_API_KEY': SECRET, 'PYTHONPATH': str(source), 'PYTHONSAFEPATH': '1'}
    with provider() as server:
        completed = subprocess.run([sys.executable, '-m', 'tracecoder.eval', 'run', '--suite',
                                    str(ROOT / 'benchmarks/tracecoder-baseline-v1/manifest.json'), '--output',
                                    str(tmp_path / 'report'), '--model', 'scripted', '--base-url',
                                    f'http://127.0.0.1:{server.server_port}/v1'], cwd=source, env=env,
                                   capture_output=True, text=True, timeout=240, check=False)
    assert completed.returncode == 0, completed.stdout + completed.stderr
    summary = json.loads((tmp_path / 'report/summary.json').read_text(encoding='utf-8'))
    assert [item['task_id'] for item in summary['results']] == IDS
    assert summary['protocol_valid'] and summary['gate_completed'] == 8
    assert summary['success_rate'] == 0 and summary['failure_counts'] == {'test_fail': 8}
    assert len(summary['preflight_checks']) == 8 and len(server.requests) == 9
    assert len(summary['gate_hashes']) == 8
    assert len(summary['agent_revisions']) == len(summary['image_ids']) == 1
    assert SECRET not in (tmp_path / 'report/summary.json').read_text(encoding='utf-8')
