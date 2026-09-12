import io
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from ghost_demo import connect
from live_demo import LiveRuns, make_handler
from test_ghost_demo import task


class LiveProgressTests(unittest.TestCase):
    def test_events_arrive_before_completion_and_queued_task_reuses(self):
        with tempfile.TemporaryDirectory() as directory:
            runs = LiveRuns(Path(directory) / 'live.sqlite3', delay=0)
            reached, release = threading.Event(), threading.Event()
            original = runs.event

            def event(job_id, stage, message, data):
                original(job_id, stage, message, data)
                if stage == 'step' and not reached.is_set():
                    reached.set()
                    release.wait(5)

            runs.event = event
            try:
                first = runs.submit(task())
                self.assertTrue(reached.wait(5))
                snapshot = runs.snapshot(first)
                self.assertEqual(snapshot['status'], 'step')
                self.assertIsNone(snapshot['result'])
                second = runs.submit(task('keyboard', 100))
                self.assertEqual(runs.snapshot(second)['status'], 'queued')
            finally:
                release.set()
                runs.executor.shutdown(wait=True)
            self.assertEqual(runs.snapshot(first)['status'], 'completed')
            self.assertEqual(runs.snapshot(second)['result']['mode'], 'reuse')
            events = runs.snapshot(first)['events']
            self.assertEqual([e['sequence'] for e in events], list(range(1, len(events) + 1)))

    def test_capacity_limit_rejects_another_run(self):
        runs = LiveRuns(Path('/tmp/not-opened.sqlite3'), delay=0)
        runs.jobs = {str(index): {} for index in range(200)}
        try:
            with self.assertRaisesRegex(ValueError, 'Session limit'):
                runs.submit(task())
        finally:
            runs.executor.shutdown(wait=True)

    def test_background_exception_becomes_failed_live_job(self):
        runs = LiveRuns(Path('/tmp/not-opened.sqlite3'), delay=0)
        with mock.patch('live_demo.connect', side_effect=RuntimeError('database unavailable')):
            job_id = runs.submit(task())
            runs.executor.shutdown(wait=True)
        snapshot = runs.snapshot(job_id)
        self.assertEqual(snapshot['status'], 'failed')
        self.assertEqual(snapshot['result']['error'], 'database unavailable')


class HandlerTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.database = Path(self.directory.name) / 'handler.sqlite3'
        db = connect(self.database)
        db.close()

        class Runs:
            database = self.database

            def snapshot(inner_self, job_id=None):
                if job_id == 'known':
                    return {'job_id': 'known', 'status': 'completed'}
                if job_id:
                    return None
                return [{'job_id': 'known', 'query': 'headphones', 'status': 'completed'}]

            def submit(inner_self, submitted):
                inner_self.submitted = submitted
                return 'new-job'

        self.runs = Runs()
        self.Handler = make_handler(self.runs)

    def tearDown(self):
        self.directory.cleanup()

    def handler(self, path='/', headers=None, body=b''):
        instance = self.Handler.__new__(self.Handler)
        instance.path = path
        instance.headers = headers or {}
        instance.rfile = io.BytesIO(body)
        instance.responses = []
        instance.respond = lambda status, data, content_type='application/json': instance.responses.append(
            (status, data, content_type))
        return instance

    def test_get_serves_page_script_runs_registry_and_errors(self):
        cases = [
            ('/', 200, 'text/html; charset=utf-8'),
            ('/flowchart.js', 200, 'text/javascript; charset=utf-8'),
            ('/api/live/runs', 200, 'application/json'),
            ('/api/live/runs/known', 200, 'application/json'),
            ('/api/live/runs/missing', 404, 'application/json'),
            ('/api/live/workflows', 200, 'application/json'),
            ('/missing', 404, 'application/json'),
        ]
        for path, expected_status, expected_type in cases:
            with self.subTest(path=path):
                handler = self.handler(path)
                handler.do_GET()
                status, data, content_type = handler.responses[0]
                self.assertEqual(status, expected_status)
                self.assertEqual(content_type, expected_type)
                if path == '/':
                    self.assertIn(b'Workflow explorer', data)
                if path == '/flowchart.js':
                    self.assertIn(b'bindPanning', data)

    def test_post_accepts_valid_task(self):
        body = json.dumps(task()).encode()
        handler = self.handler('/api/live/runs', {
            'Content-Length': str(len(body)),
            'Origin': 'http://127.0.0.1:8765',
            'Host': '127.0.0.1:8765',
        }, body)
        handler.do_POST()
        self.assertEqual(handler.responses, [(202, {'job_id': 'new-job'}, 'application/json')])
        self.assertEqual(self.runs.submitted, task())

    def test_post_rejects_wrong_path_origin_length_and_json(self):
        cases = [
            ('/wrong', {}, b'{}', 404),
            ('/api/live/runs', {'Origin': 'https://example.com', 'Host': '127.0.0.1'}, b'{}', 403),
            ('/api/live/runs', {'Content-Length': '0'}, b'', 400),
            ('/api/live/runs', {'Content-Length': '1'}, b'{', 400),
        ]
        for path, headers, body, expected in cases:
            with self.subTest(expected=expected):
                handler = self.handler(path, headers, body)
                handler.do_POST()
                self.assertEqual(handler.responses[0][0], expected)

    def test_respond_writes_headers_and_json_body(self):
        handler = self.Handler.__new__(self.Handler)
        handler.wfile = io.BytesIO()
        statuses, headers = [], []
        handler.send_response = statuses.append
        handler.send_header = lambda key, value: headers.append((key, value))
        handler.end_headers = lambda: None

        self.Handler.respond(handler, 201, {'ok': True})

        self.assertEqual(statuses, [201])
        self.assertEqual(json.loads(handler.wfile.getvalue()), {'ok': True})
        self.assertIn(('Cache-Control', 'no-store'), headers)
        self.assertIn(('X-Content-Type-Options', 'nosniff'), headers)
        self.Handler.log_message(handler, 'ignored')


if __name__ == '__main__':
    unittest.main()
