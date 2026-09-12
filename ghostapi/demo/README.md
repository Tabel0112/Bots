# Ghost workflow demo

## Live workflow demo

From the `hackathon-team` project directory:

```sh
python3 ghostapi/demo/live_demo.py
```

Open [the local viewer](http://127.0.0.1:8765) to watch matching, executed steps, results, and qualification on an interactive flowchart while the task runs. Drag the empty chart canvas with the mouse or a pointer to pan around. Click nodes to inspect their evidence, use the history slider or Step buttons to revisit execution, adjust zoom, and select Follow live to resume tracking. Additional tasks queue while the viewer remains responsive. The browser/catalog are simulated; events and database writes come from the running demo. Stop the server with Ctrl+C.


Run from the project root with Python 3; no installation or API keys needed:

```sh
python3 ghostapi/demo/ghost_demo.py
```

1. Search `headphones` with a maximum price of `150`. On an empty database, Ghost finds no match, uses simulated discovery, executes and validates a procedure, saves a candidate, and qualifies it with three fixture replays.
2. Search `keyboard` with a maximum price of `100`. Ghost selects the saved qualified workflow and binds the new inputs. You should get two keyboards.
3. Search `headphones` at `50`. The same workflow returns a verified empty result.
4. Enter `/workflows` to inspect version/status, or `/quit` to exit. Restart the app to check that the saved workflow persists.

The first search returns Studio headphones (CAD 129) and Travel headphones (CAD 79). The simulated site uses `.invalid` URLs; these are deliberately not live product links.

For structured task input and the full result, including steps and evidence:

```sh
python3 ghostapi/demo/ghost_demo.py --task ghostapi/demo/task.json
python3 ghostapi/demo/ghost_demo.py --list
```

Edit `ghostapi/demo/task.json` to change inputs. Unknown parameters are rejected instead of silently ignored. Unsupported sites/operations return an explicit failure. Set `mode` to `explore` to force discovery and create another candidate version under the same workflow identity.

To test candidate-only storage or start a separate empty database:

```sh
python3 ghostapi/demo/ghost_demo.py --db /tmp/ghost-candidate-demo.sqlite3 --skip-qualification
```

Candidates are not selected automatically, so another search without a qualified version triggers discovery again. Qualification here means fresh, isolated **fixture executions**, not actual cloud-browser sessions.

## What is real and what is simulated

Real: SQLite persistence, compatibility filtering, parameter binding, ordered step execution, result validation against fixture ground truth, candidate/qualified states, version history, and execution records. Data stays on your computer in `ghostapi/demo/ghost.sqlite3` by default.

Simulated: the discovery adapter supplies a known procedure, and its executor operates on an in-memory catalog. There is no LLM-generated workflow, real ARGUS orchestration, Steel session, live website, or repair agent yet. The local viewer exposes a small demo HTTP API, separate from the planned shared API. The next integration is to replace `simulated_discovery` and `replay` with the agent/browser interfaces while preserving storage and matching behavior.

This is a single-process demo. It does not implement concurrent workflow promotion or request-id idempotency; submitting the same request ID executes another run.

Run the focused lifecycle checks:

```sh
python3 -m unittest discover -s ghostapi/demo -p 'test_*.py' -v
node --test ghostapi/demo/test_flowchart.js
```

Generate the same reports uploaded to Codecov:

```sh
python3 -m pip install -r ghostapi/requirements-dev.txt
python3 -m coverage run --rcfile=ghostapi/.coveragerc -m unittest discover -s ghostapi/demo -p 'test_*.py'
python3 -m coverage xml --rcfile=ghostapi/.coveragerc
node --test --experimental-test-coverage --test-coverage-include='ghostapi/demo/flowchart.js' --test-coverage-exclude='**/test_*.js' --test-reporter=spec --test-reporter-destination=stdout --test-reporter=lcov --test-reporter-destination=ghostapi/coverage-flowchart.lcov ghostapi/demo/test_flowchart.js
```

The repository workflow uploads the Python XML and JavaScript LCOV reports as separate Codecov flags. It uses Codecov OIDC, so it does not store an upload token in the repository.
