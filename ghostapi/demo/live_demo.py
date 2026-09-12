"""Local live workflow viewer. Start with python3 ghostapi/demo/live_demo.py."""
import argparse
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse
from uuid import uuid4

from ghost_demo import DEFAULT_DB, connect, run_task, timestamp, validate_request


class LiveRuns:
    def __init__(self, database, delay=0.35):
        self.database, self.delay = database, delay
        self.jobs, self.lock = {}, threading.Lock()
        # Preserve the prototype's single-writer semantics. HTTP progress reads
        # continue concurrently; additional submitted executions wait in order.
        self.executor = ThreadPoolExecutor(max_workers=1)

    def submit(self, task):
        validate_request(task)
        job_id = str(uuid4())
        with self.lock:
            if len(self.jobs) >= 200:
                raise ValueError('Session limit reached. Restart the demo to clear its live history.')
            self.jobs[job_id] = {'job_id': job_id, 'task': task, 'status': 'queued', 'events': [], 'result': None}
        self.event(job_id, 'queued', 'Task queued for execution.', {})
        self.executor.submit(self.execute, job_id, task)
        return job_id

    def event(self, job_id, stage, message, data):
        with self.lock:
            job = self.jobs[job_id]
            job['events'].append({'sequence': len(job['events']) + 1, 'timestamp': timestamp(),
                                  'stage': stage, 'message': message, 'data': data})
            job['status'] = stage
            if 'result' in data:
                job['result'] = data['result']

    def execute(self, job_id, task):
        def emit(stage, message, data):
            self.event(job_id, stage, message, data)
            if stage not in ('completed', 'failed'):
                time.sleep(self.delay)  # Deliberate presentation pacing, not measured browser latency.
        db = None
        try:
            db = connect(self.database)
            run_task(db, task, emit=emit)
        except Exception as error:
            self.event(job_id, 'failed', str(error), {'result': {'status': 'failed', 'error': str(error)}})
        finally:
            if db is not None:
                db.close()

    def snapshot(self, job_id=None):
        with self.lock:
            if job_id:
                return json.loads(json.dumps(self.jobs.get(job_id)))
            return [{'job_id': j['job_id'], 'query': j['task']['parameters']['query'], 'status': j['status']}
                    for j in reversed(list(self.jobs.values()))]


def make_handler(runs):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def respond(self, status, data, content_type='application/json'):
            body = data if isinstance(data, bytes) else json.dumps(data).encode()
            self.send_response(status)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            path = urlparse(self.path).path
            if path == '/':
                self.respond(200, Path(__file__).with_name('live.html').read_bytes(), 'text/html; charset=utf-8')
            elif path == '/flowchart.js':
                self.respond(200, Path(__file__).with_name('flowchart.js').read_bytes(), 'text/javascript; charset=utf-8')
            elif path == '/api/live/runs':
                self.respond(200, runs.snapshot())
            elif path.startswith('/api/live/runs/'):
                job = runs.snapshot(path.rsplit('/', 1)[-1])
                self.respond(200 if job else 404, job or {'error': 'Run not found'})
            elif path == '/api/live/workflows':
                db = connect(runs.database)
                try:
                    self.respond(200, [dict(r) for r in db.execute('SELECT skill_id, version, status FROM workflow_versions ORDER BY version DESC')])
                finally:
                    db.close()
            else:
                self.respond(404, {'error': 'Not found'})

        def do_POST(self):
            if self.path != '/api/live/runs':
                self.respond(404, {'error': 'Not found'})
                return
            origin = self.headers.get('Origin')
            if origin and origin != 'http://' + self.headers.get('Host', ''):
                self.respond(403, {'error': 'Use the local demo page to submit tasks.'})
                return
            try:
                length = int(self.headers.get('Content-Length', '0'))
                if not 0 < length <= 16384:
                    raise ValueError('Task body must be between 1 and 16384 bytes.')
                job_id = runs.submit(json.loads(self.rfile.read(length)))
                self.respond(202, {'job_id': job_id})
            except (ValueError, TypeError) as error:
                self.respond(400, {'error': str(error)})
    return Handler


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--db', type=Path, default=DEFAULT_DB)
    args = parser.parse_args()
    db = connect(args.db)
    db.close()
    runs = LiveRuns(args.db)
    server = ThreadingHTTPServer(('127.0.0.1', args.port), make_handler(runs))
    print(f'Ghost live demo: http://127.0.0.1:{server.server_port}', flush=True)
    print('Simulated browser. Real workflow events and SQLite storage. Ctrl+C to stop.', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        runs.executor.shutdown(wait=True)


if __name__ == '__main__':
    main()
