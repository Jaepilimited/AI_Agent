"""Save reproducible focused and full-suite verification output for this release."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
TASK = Path(__file__).resolve().parent
FOCUSED = ['tests/test_pr_issues.py', 'tests/test_pr_routing.py', 'tests/test_pr_monitoring.py',
           'tests/test_data_freshness.py', 'tests/test_notion_grounding.py',
           'tests/test_source_selection.py', 'tests/test_source_alias_scope.py',
           'tests/test_route_source_gate.py', 'tests/test_single_source_fast_path.py',
           'tests/test_authoring_request_routing.py', 'tests/test_pasted_data_routing.py',
           'tests/test_routing_scope_guards.py', 'tests/test_awards.py']

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('kind', choices=['focused', 'full_suite'])
    args = parser.parse_args()
    targets = FOCUSED if args.kind == 'focused' else ['tests/', '--ignore=tests/frontend']
    command = [sys.executable, '-m', 'pytest', *targets, '-q', '--tb=short']
    log = TASK / (args.kind + '.log')
    with log.open('w', encoding='utf-8') as output:
        result = subprocess.run(command, cwd=ROOT, stdout=output, stderr=subprocess.STDOUT,
                                env=dict(os.environ, PYTHONIOENCODING='utf-8'))
    (TASK / (args.kind + '_result.json')).write_text(
        json.dumps({'command': command, 'exit_code': result.returncode}, indent=2), encoding='utf-8')
    sys.stdout.reconfigure(encoding='utf-8')
    print('\n'.join(log.read_text(encoding='utf-8', errors='replace').splitlines()[-35:]))
    raise SystemExit(result.returncode)
