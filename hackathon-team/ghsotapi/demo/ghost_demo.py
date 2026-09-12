"""Dependency-free Ghost workflow demo. Browser and discovery are simulated."""
import argparse
import json
import sqlite3
from pathlib import Path
from datetime import datetime, timezone
from uuid import uuid4

DEFAULT_DB = Path(__file__).with_name('ghost.sqlite3')
CATALOG = [
    {'title': 'Studio headphones', 'price': 129, 'category': 'headphones'},
    {'title': 'Travel headphones', 'price': 79, 'category': 'headphones'},
    {'title': 'Reference headphones', 'price': 249, 'category': 'headphones'},
    {'title': 'Mechanical keyboard', 'price': 99, 'category': 'keyboard'},
    {'title': 'Compact keyboard', 'price': 49, 'category': 'keyboard'},
    {'title': 'Wireless mouse', 'price': 35, 'category': 'mouse'},
]


def timestamp():
    return datetime.now(timezone.utc).isoformat()


def connect(path):
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    db.executescript('''
        CREATE TABLE IF NOT EXISTS workflows (
            skill_id TEXT PRIMARY KEY, site_id TEXT NOT NULL,
            operation TEXT NOT NULL, description TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS workflow_lookup ON workflows(site_id, operation);
        CREATE TABLE IF NOT EXISTS workflow_versions (
            skill_id TEXT NOT NULL, version INTEGER NOT NULL, status TEXT NOT NULL,
            definition TEXT NOT NULL, qualification TEXT NOT NULL,
            created_at TEXT NOT NULL, PRIMARY KEY(skill_id, version)
        );
        CREATE TABLE IF NOT EXISTS workflow_runs (
            run_id TEXT PRIMARY KEY, request_id TEXT NOT NULL, mode TEXT NOT NULL,
            request TEXT NOT NULL, result TEXT NOT NULL, created_at TEXT NOT NULL
        );
    ''')
    return db


def validate_request(task):
    if not isinstance(task, dict):
        raise ValueError('Task must be a JSON object.')
    required = {'schema_version', 'request_id', 'site_id', 'operation', 'parameters', 'mode'}
    if set(task) != required:
        raise ValueError('Task fields must be exactly: ' + ', '.join(sorted(required)))
    if task['schema_version'] != '0.1' or task['mode'] not in ('auto', 'explore'):
        raise ValueError('Use schema_version 0.1 and mode auto or explore.')
    for key in ('request_id', 'site_id', 'operation'):
        if not isinstance(task[key], str) or not task[key].strip():
            raise ValueError(f'{key} must be a nonempty string.')
    params = task['parameters']
    if not isinstance(params, dict) or set(params) != {'query', 'max_price'}:
        raise ValueError('Supported inputs are exactly query and max_price; filters cannot be ignored.')
    if not isinstance(params['query'], str) or not params['query'].strip():
        raise ValueError('query must be a nonempty string.')
    price = params['max_price']
    if type(price) not in (float, int) or not 0 <= price <= 1_000_000:
        raise ValueError('max_price must be a finite number between 0 and 1000000 CAD.')


def simulated_discovery(task):
    # This adapter represents a recorded successful agent trace. It is deliberately
    # bounded to one local fixture; it does not claim to learn arbitrary websites.
    return {
        'inputs': {'query': 'string', 'max_price': 'number'},
        'output_schema_id': 'product-list.v1',
        'validator_id': 'demo-catalog.v1',
        'action_class': 'read_only',
        'preconditions': ['fixture catalog is available'],
        'steps': [
            {'action': 'navigate', 'path': '/catalog'},
            {'action': 'fill', 'target': 'Search products', 'value': {'parameter': 'query'}},
            {'action': 'fill', 'target': 'Maximum price', 'value': {'parameter': 'max_price'}},
            {'action': 'click', 'target': 'Search'},
            {'action': 'extract', 'schema': 'product-list.v1'},
        ],
        'source': 'simulated discovery adapter',
    }


def replay(definition, params, emit=None):
    fields, items, trace = {}, None, []
    for step in definition['steps']:
        action = step['action']
        if action == 'navigate':
            if step['path'] != '/catalog':
                raise ValueError('Unsupported fixture path')
        elif action == 'fill':
            fields[step['target']] = params[step['value']['parameter']]
        elif action == 'click':
            query = fields['Search products'].strip().lower()
            limit = fields['Maximum price']
            items = [dict(row, currency='CAD', url=f'https://demo-catalog.invalid/products/{i}')
                     for i, row in enumerate(CATALOG)
                     if query in row['title'].lower() and row['price'] <= limit]
        elif action == 'extract':
            if items is None:
                raise ValueError('No search results to extract')
        else:
            raise ValueError('Unsupported action: ' + action)
        trace.append(dict(step, observed_at=timestamp(), outcome='succeeded'))
        if emit:
            emit('step', action + ': ' + step.get('target', step.get('path', step.get('schema', ''))),
                 {'step': step, 'fields': dict(fields), 'items': items})
    if items is None:
        raise ValueError('Incomplete workflow')
    return items, {'source': 'simulated local catalog', 'observed_at': timestamp(),
                   'applied_inputs': {'query': fields['Search products'], 'max_price': fields['Maximum price']},
                   'empty_state': len(items) == 0, 'trace': trace}


def verify(params, items, evidence):
    # Fixture ground truth is independent of step execution and bindings.
    expected = {row['title'] for row in CATALOG
                if params['query'].strip().casefold() in row['title'].casefold()
                and row['price'] <= params['max_price']}
    checks = {
        'inputs_applied': evidence['applied_inputs'] == params,
        'expected_result_set': {row['title'] for row in items} == expected,
        'no_duplicates': len(items) == len({row['title'] for row in items}),
        'price_and_currency': all(row['price'] <= params['max_price'] and row['currency'] == 'CAD' for row in items),
        'empty_state': bool(items) or evidence['empty_state'],
    }
    return {'status': 'passed' if all(checks.values()) else 'failed', 'checks': checks,
            'scope': 'synthetic fixture only; no live website verification'}


def qualify(db, skill_id, version, emit=None):
    row = db.execute('SELECT * FROM workflow_versions WHERE skill_id=? AND version=?',
                     (skill_id, version)).fetchone()
    reports = []
    for query, max_price in [('keyboard', 100), ('mouse', 40), ('no-such-product', 10)]:
        params = {'query': query, 'max_price': max_price}
        if emit:
            emit('qualifying', 'Testing ' + query, {'parameters': params})
        items, evidence = replay(json.loads(row['definition']), params)
        report = {'run_id': str(uuid4()), 'parameters': params,
                  'validation': verify(params, items, evidence), 'evidence': evidence}
        reports.append(report)
        db.execute('INSERT INTO workflow_runs VALUES (?, ?, ?, ?, ?, ?)',
                   (report['run_id'], 'qualification', 'qualification', json.dumps(params),
                    json.dumps(dict(report, skill_id=skill_id, version=version)), timestamp()))
    passed = all(r['validation']['status'] == 'passed' for r in reports)
    status = 'qualified' if passed else 'candidate'
    db.execute('UPDATE workflow_versions SET status=?, qualification=? WHERE skill_id=? AND version=?',
               (status, json.dumps(reports), skill_id, version))
    return status


def run_task(db, task, auto_qualify=True, emit=None):
    validate_request(task)
    run_id, events = str(uuid4()), []

    def report(stage, message, data=None):
        events.append(message)
        if emit:
            emit(stage, message, data or {})

    report('matching', 'Searching the workflow database.')
    if task['site_id'] != 'demo-catalog' or task['operation'] != 'search_products':
        result = {'run_id': run_id, 'status': 'failed', 'error': 'UNSUPPORTED_DISCOVERY',
                  'message': 'This simulated discovery adapter supports demo-catalog / search_products only.',
                  'events': ['No suitable workflow; no discovery adapter for this task.']}
    else:
        candidates = db.execute('''SELECT v.* FROM workflow_versions v JOIN workflows w USING(skill_id)
            WHERE w.site_id=? AND w.operation=? AND v.status='qualified'
            ORDER BY v.created_at DESC, v.version DESC''', (task['site_id'], task['operation'])).fetchall()
        match = None
        for row in candidates:
            d = json.loads(row['definition'])
            if (d['inputs'] == {'query': 'string', 'max_price': 'number'}
                    and d['output_schema_id'] == 'product-list.v1'
                    and d['validator_id'] == 'demo-catalog.v1' and d['action_class'] == 'read_only'):
                match = row
                break
        if task['mode'] == 'explore':
            match = None
            report('exploring', 'Exploration forced by request.')
        if match:
            skill_id, version = match['skill_id'], match['version']
            definition, mode = json.loads(match['definition']), 'reuse'
            report('replaying', f'MATCH: {skill_id} v{version}; compatible qualified workflow.')
        else:
            mode, skill_id = 'exploration', 'demo-catalog.search-products'
            version = db.execute('SELECT COALESCE(MAX(version),0)+1 FROM workflow_versions WHERE skill_id=?',
                                 (skill_id,)).fetchone()[0]
            report('exploring', 'NO MATCH: no suitable qualified workflow. Starting simulated discovery.')
            definition = simulated_discovery(task)
        items, evidence = replay(definition, task['parameters'], report)
        report('validating', 'Checking results and input evidence.')
        validation = verify(task['parameters'], items, evidence)
        report('validating', 'Executed parameterized steps against the fixture catalog.')
        report('validated', 'Validation: ' + validation['status'], {'validation': validation, 'items': items})
        workflow_status = match['status'] if match else None
        if not match and validation['status'] == 'passed':
            definition['source_run_ids'] = [run_id]
            db.execute('INSERT OR IGNORE INTO workflows VALUES (?, ?, ?, ?)',
                       (skill_id, task['site_id'], task['operation'], 'Search fixture products by query and maximum price'))
            db.execute('INSERT INTO workflow_versions VALUES (?, ?, ?, ?, ?, ?)',
                       (skill_id, version, 'candidate', json.dumps(definition), '[]', timestamp()))
            workflow_status = 'candidate'
            report('compiling', f'STAGED: {skill_id} v{version} candidate; commit follows qualification.')
            if auto_qualify:
                workflow_status = qualify(db, skill_id, version, report)
                report('qualified', 'Three isolated fixture replays, including an empty result: ' + workflow_status)
        result = {'run_id': run_id, 'status': 'succeeded' if validation['status'] == 'passed' else 'failed',
                  'mode': mode, 'skill_id': skill_id, 'version': version,
                  'workflow_status': workflow_status, 'items': items, 'validation': validation,
                  'evidence': evidence, 'events': events, 'simulation': True}
    db.execute('INSERT INTO workflow_runs VALUES (?, ?, ?, ?, ?, ?)',
               (run_id, task['request_id'], result.get('mode', 'exploration'), json.dumps(task), json.dumps(result), timestamp()))
    db.commit()
    if emit:
        emit('completed' if result['status'] == 'succeeded' else 'failed',
             'Execution finished; run record committed.', {'result': result})
    return result


def print_result(result):
    for event in result['events']:
        print('  ' + event)
    if result['status'] != 'succeeded':
        print('  FAILED: ' + result.get('message', 'Validation failed'))
        return
    print(f"\n  Result: {len(result['items'])} products • {result['mode']} • workflow {result['workflow_status']}")
    for item in result['items']:
        print(f"    {item['title']:<28} CAD {item['price']:.2f}")
    print('  Run: ' + result['run_id'])


def list_workflows(db):
    rows = db.execute('SELECT skill_id, version, status FROM workflow_versions ORDER BY skill_id, version').fetchall()
    if not rows:
        print('  Database is empty. Run your first search to create a workflow.')
    for row in rows:
        print(f"  {row['skill_id']}  v{row['version']}  {row['status']}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db', type=Path, default=DEFAULT_DB)
    parser.add_argument('--task', type=Path, help='Execute a JSON task file and print its full JSON result')
    parser.add_argument('--list', action='store_true', help='List saved workflow versions')
    parser.add_argument('--skip-qualification', action='store_true', help='Save new workflows as candidates only')
    args = parser.parse_args()
    with connect(args.db) as db:
        if args.list:
            list_workflows(db)
            return
        if args.task:
            try:
                result = run_task(db, json.loads(args.task.read_text()), not args.skip_qualification)
                print(json.dumps(result, indent=2))
                raise SystemExit(0 if result['status'] == 'succeeded' else 1)
            except (ValueError, OSError) as error:
                print(json.dumps({'status': 'failed', 'error': str(error)}))
                raise SystemExit(1)
        print('\nGHOST / workflow memory demo')
        print('Real SQLite storage. Simulated browser, discovery, and products. Prices in CAD.')
        print('Try headphones at 150, then keyboard at 100 to see reuse.')
        print('Commands: /workflows, /quit. No external services or API keys needed.')
        print('Database: ' + str(args.db.resolve()))
        while True:
            try:
                query = input('\nSearch query > ').strip()
                if query == '/quit':
                    break
                if query == '/workflows':
                    list_workflows(db)
                    continue
                price = float(input('Maximum price [150] > ').strip() or '150')
                task = {'schema_version': '0.1', 'request_id': str(uuid4()), 'site_id': 'demo-catalog',
                        'operation': 'search_products', 'parameters': {'query': query, 'max_price': price}, 'mode': 'auto'}
                print_result(run_task(db, task, not args.skip_qualification))
            except (ValueError, OSError) as error:
                print('  Input error: ' + str(error))
            except (EOFError, KeyboardInterrupt):
                print('\nSaved workflows will be available next time.')
                break


if __name__ == '__main__':
    main()
