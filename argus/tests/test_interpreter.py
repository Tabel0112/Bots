"""Tests for stage 1, the interpreter.

No test here touches the network.  Every test injects a
:class:`argus.fakes.FakeModelClient` scripted with a canned
:class:`argus.model_client.ModelResult`, so the module's own behaviour - span
verification, the refusal and truncation mapping, the client it builds when
given none, and the conversion to the dataclasses - is what is under test, not
the model's judgement.

The parsed payload is a plain ``dict``, because that is what the model boundary
hands back: ``ModelResult.parsed`` is the validated pydantic model's
``model_dump()``.  Which SDK produced it, and how, is
``argus/tests/test_model_client.py``'s business.
"""

from __future__ import annotations

import os
import unittest
from unittest import mock

from argus import registry
from argus.contracts import ContractError, InterpretedRequest
from argus.fakes import FakeModelClient
from argus.interpreter import MAX_TOKENS, interpret, system_prompt
from argus.model_client import ModelResult

TEXT = "Find headphones under $150 in the demo catalog"
COMPOUND = "Find headphones under $150 and also search for keyboards"

#: The model the scripted results claim answered; the interpreter must record
#: exactly this rather than resolving a name of its own.
MODEL = "test-model-1"


# --------------------------------------------------------------------------- #
# The canned payload
# --------------------------------------------------------------------------- #


def parameter(name, value, *, source="text_span", confidence=0.95, span=None):
    start, end = span if span else (None, None)
    return {
        "name": name,
        "value": value,
        "source": source,
        "confidence": confidence,
        "span_start": start,
        "span_end": end,
    }


def intent(parameters, *, site_id="demo-catalog", operation="search_products", confidence=0.94):
    return {
        "site_id": site_id,
        "operation": operation,
        "parameters": list(parameters),
        "confidence": confidence,
    }


def criterion(text, kind, *, parameter=None, span=None, confidence=0.95):
    start, end = span if span else (None, None)
    return {
        "text": text,
        "kind": kind,
        "parameter": parameter,
        "span_start": start,
        "span_end": end,
        "confidence": confidence,
    }


def open_intent(
    parameters=(),
    *,
    site_id="jobs.example.com",
    operation="find_jobs",
    confidence=0.9,
    kind="open",
    target_domain="jobs.example.com",
    goal="Find software engineering jobs",
    criteria=(),
    expected_record_shape=("title", "company", "url"),
):
    """A tier-two intent as ``IntentOut`` returns it, open fields included."""
    return {
        "site_id": site_id,
        "operation": operation,
        "parameters": list(parameters),
        "confidence": confidence,
        "kind": kind,
        "target_domain": target_domain,
        "goal": goal,
        "criteria": list(criteria),
        "expected_record_shape": list(expected_record_shape),
    }


def missing(intent_index, parameter_name, question):
    return {
        "intent_index": intent_index,
        "parameter": parameter_name,
        "question": question,
    }


def payload(intents=(), missing_required=(), ambiguities=()):
    return {
        "intents": list(intents),
        "missing_required": list(missing_required),
        "ambiguities": list(ambiguities),
    }


def run(text, parsed, *, request_id="request-test-1", model=None, status="ok", raw_text=None):
    # The boundary never carries parsed content on a non-ok status, so neither
    # does the fake: a truncated or refused call has nothing to read.
    client = FakeModelClient(
        ModelResult(
            status=status,
            parsed=parsed if status == "ok" else None,
            raw_text=raw_text,
            model=MODEL,
        )
    )
    interpreted = interpret(text, request_id, client=client, model=model)
    return interpreted, client


class InterpreterTest(unittest.TestCase):
    def setUp(self):
        # ARGUS_MODEL must not leak in from the developer's shell.
        patcher = mock.patch.dict(os.environ, {}, clear=False)
        patcher.start()
        os.environ.pop("ARGUS_MODEL", None)
        self.addCleanup(patcher.stop)

    # -- the prompt ------------------------------------------------------- #

    def test_system_prompt_lists_the_registry(self):
        prompt = system_prompt()
        for site_id, site in registry.SITES.items():
            self.assertIn(site_id, prompt)
            self.assertIn(site["origin"], prompt)
        for name, spec in registry.OPERATIONS.items():
            self.assertIn(name, prompt)
            for parameter_name, rule in spec["parameters"].items():
                self.assertIn(parameter_name, prompt)
                self.assertIn(rule["type"], prompt)
        self.assertIn("required", prompt)
        self.assertIn("ambiguit", prompt)

    # -- single intent ---------------------------------------------------- #

    def test_single_intent(self):
        parsed = payload(
            intents=[
                intent(
                    [
                        parameter("query", "headphones", confidence=0.96, span=(5, 15)),
                        parameter("max_price", 150, confidence=0.92, span=(22, 26)),
                    ]
                )
            ]
        )
        interpreted, client = run(TEXT, parsed)

        self.assertEqual(interpreted.request_id, "request-test-1")
        self.assertEqual(interpreted.raw_text, TEXT)
        self.assertEqual(interpreted.model, MODEL)
        self.assertEqual(len(interpreted.intents), 1)
        self.assertEqual(interpreted.ambiguities, [])
        self.assertEqual(interpreted.missing_required, [])

        only = interpreted.intents[0]
        self.assertEqual(only.site_id, "demo-catalog")
        self.assertEqual(only.operation, "search_products")
        self.assertEqual(sorted(only.parameters), ["max_price", "query"])
        self.assertEqual(only.parameters["query"].value, "headphones")
        self.assertEqual(only.parameters["query"].source, "text_span")
        self.assertEqual(only.parameters["query"].span, (5, 15))
        # "$150" cited for 150 is the same value, so the span stands.
        self.assertEqual(only.parameters["max_price"].span, (22, 26))
        self.assertEqual(only.parameters["max_price"].source, "text_span")

        # And it round-trips as the handoff artifact.
        self.assertEqual(
            InterpretedRequest.from_dict(interpreted.to_dict()), interpreted
        )

    def test_request_shape_sent_to_the_model(self):
        parsed = payload(intents=[intent([parameter("query", "headphones", span=(5, 15))])])
        _, client = run(TEXT, parsed)
        self.assertEqual(len(client.calls), 1)
        call = client.calls[0]

        # Exactly the boundary's four arguments; the model name is the client's
        # business, so the interpreter does not pass one.
        self.assertEqual(
            sorted(call), ["max_tokens", "output_model", "system", "user"]
        )
        self.assertEqual(call["max_tokens"], MAX_TOKENS)
        self.assertEqual(MAX_TOKENS, 4096)
        # The user turn is the request text, unwrapped and unprefixed.
        self.assertEqual(call["user"], TEXT)
        self.assertEqual(call["system"], system_prompt())
        self.assertIn("search_products", call["system"])
        # A pydantic schema, not a prose instruction to return JSON.
        self.assertTrue(hasattr(call["output_model"], "model_validate"))
        self.assertIn("intents", call["output_model"].model_fields)

    # -- compound request ------------------------------------------------- #

    def test_compound_request_becomes_two_intents(self):
        parsed = payload(
            intents=[
                intent(
                    [
                        parameter("query", "headphones", span=(5, 15)),
                        parameter("max_price", 150, span=(22, 26)),
                    ]
                ),
                intent([parameter("query", "keyboards", span=(47, 56))], confidence=0.88),
            ]
        )
        interpreted, _ = run(COMPOUND, parsed)

        self.assertEqual(len(interpreted.intents), 2)
        self.assertEqual(interpreted.intents[0].parameters["query"].value, "headphones")
        second = interpreted.intents[1]
        self.assertEqual(second.parameters["query"].value, "keyboards")
        self.assertEqual(second.parameters["query"].span, (47, 56))
        self.assertEqual(COMPOUND[47:56], "keyboards")
        self.assertEqual(interpreted.ambiguities, [])

    # -- missing required parameter --------------------------------------- #

    def test_missing_required_parameter_is_reported_not_guessed(self):
        text = "Find something cheap in the demo catalog"
        parsed = payload(
            intents=[intent([parameter("max_price", 20, source="structured")])],
            missing_required=[
                missing(0, "query", "What product should I search the demo catalog for?")
            ],
            ambiguities=["'cheap' is not a price the catalog can filter on."],
        )
        interpreted, _ = run(text, parsed)

        self.assertNotIn("query", interpreted.intents[0].parameters)
        self.assertEqual(len(interpreted.missing_required), 1)
        entry = interpreted.missing_required[0]
        self.assertEqual((entry.intent_index, entry.parameter), (0, "query"))
        self.assertEqual(
            entry.question, "What product should I search the demo catalog for?"
        )
        self.assertEqual(len(interpreted.ambiguities), 1)
        # A structured value carries no span, per ParameterOrigin's contract.
        self.assertIsNone(interpreted.intents[0].parameters["max_price"].span)

    # -- unsupported site ------------------------------------------------- #

    def test_unsupported_site_is_passed_through_for_the_gate(self):
        text = "Find headphones on example-shop"
        parsed = payload(
            intents=[
                intent(
                    [parameter("query", "headphones", span=(5, 15))],
                    site_id="example-shop",
                    confidence=0.4,
                )
            ],
            ambiguities=["example-shop is not one of the supported sites."],
        )
        interpreted, _ = run(text, parsed)

        # The interpreter reports, it does not judge: the gate rejects this.
        self.assertEqual(interpreted.intents[0].site_id, "example-shop")
        self.assertNotIn("example-shop", registry.SITES)
        self.assertEqual(len(interpreted.ambiguities), 1)

    # -- refusal ---------------------------------------------------------- #

    def test_refusal_raises_model_refused_and_carries_the_reason(self):
        client = FakeModelClient(
            ModelResult(status="refusal", raw_text="declined: cyber", model=MODEL)
        )
        with self.assertRaises(ContractError) as caught:
            interpret(TEXT, "request-test-1", client=client)
        self.assertEqual(caught.exception.code, "MODEL_REFUSED")
        self.assertEqual(caught.exception.typed_error.code, "MODEL_REFUSED")
        self.assertIn("cyber", str(caught.exception))
        # A refusal carries no content at all; the boundary enforces it, and
        # the interpreter never looks for any.
        self.assertIsNone(client.results[0].parsed)

    def test_truncated_response_is_an_extraction_failure(self):
        parsed = payload(intents=[intent([parameter("query", "headphones", span=(5, 15))])])
        with self.assertRaises(ContractError) as caught:
            run(TEXT, parsed, status="truncated")
        self.assertEqual(caught.exception.code, "EXTRACTION_FAILED")
        self.assertIn("cut off", str(caught.exception))

    def test_invalid_response_is_an_extraction_failure(self):
        with self.assertRaises(ContractError) as caught:
            run(TEXT, None, status="invalid", raw_text="{\"intents\": ")
        self.assertEqual(caught.exception.code, "EXTRACTION_FAILED")

    def test_an_ok_result_with_no_payload_is_an_extraction_failure(self):
        with self.assertRaises(ContractError) as caught:
            run(TEXT, None)
        self.assertEqual(caught.exception.code, "EXTRACTION_FAILED")

    # -- span verification ------------------------------------------------ #

    def test_span_that_does_not_match_is_downgraded(self):
        parsed = payload(
            intents=[
                intent(
                    [
                        # "under" is not where "headphones" came from.
                        parameter("query", "headphones", confidence=0.9, span=(16, 21)),
                        parameter("max_price", 150, confidence=0.92, span=(22, 26)),
                    ]
                )
            ]
        )
        interpreted, _ = run(TEXT, parsed)
        query = interpreted.intents[0].parameters["query"]
        self.assertEqual(query.value, "headphones")
        self.assertEqual(query.source, "structured")
        self.assertEqual(query.confidence, 0.0)
        self.assertIsNone(query.span)
        self.assertEqual(len(interpreted.ambiguities), 1)
        self.assertIn("query", interpreted.ambiguities[0])
        # The parameter whose span did match is untouched.
        self.assertEqual(interpreted.intents[0].parameters["max_price"].source, "text_span")

    def test_span_out_of_range_or_absent_is_downgraded(self):
        parsed = payload(
            intents=[
                intent(
                    [
                        parameter("query", "headphones", span=(500, 510)),
                        parameter("max_price", 150, source="text_span", span=None),
                    ]
                )
            ]
        )
        interpreted, _ = run(TEXT, parsed)
        for name in ("query", "max_price"):
            origin = interpreted.intents[0].parameters[name]
            self.assertEqual(origin.source, "structured", name)
            self.assertEqual(origin.confidence, 0.0, name)
            self.assertIsNone(origin.span, name)
        self.assertEqual(len(interpreted.ambiguities), 2)

    def test_case_and_whitespace_do_not_break_a_span(self):
        text = "Find  Headphones  in the demo catalog"
        parsed = payload(
            intents=[intent([parameter("query", "headphones", span=(5, 18))])]
        )
        interpreted, _ = run(text, parsed)
        self.assertEqual(interpreted.intents[0].parameters["query"].source, "text_span")
        self.assertEqual(interpreted.intents[0].parameters["query"].span, (5, 18))

    def test_duplicate_parameter_keeps_the_first_and_notes_it(self):
        parsed = payload(
            intents=[
                intent(
                    [
                        parameter("query", "headphones", span=(5, 15)),
                        parameter("query", "earphones", span=(5, 15)),
                    ]
                )
            ]
        )
        interpreted, _ = run(TEXT, parsed)
        self.assertEqual(interpreted.intents[0].parameters["query"].value, "headphones")
        self.assertEqual(len(interpreted.ambiguities), 1)
        self.assertIn("more than once", interpreted.ambiguities[0])

    def test_integer_parameter_narrowed_from_a_json_number(self):
        parsed = payload(
            intents=[
                intent(
                    [
                        parameter("query", "headphones", span=(5, 15)),
                        parameter("max_results", 3.0, source="structured"),
                    ]
                )
            ]
        )
        interpreted, _ = run(TEXT, parsed)
        value = interpreted.intents[0].parameters["max_results"].value
        self.assertIsInstance(value, int)
        self.assertEqual(value, 3)

    # -- which model answered ---------------------------------------------- #

    def test_the_recorded_model_is_the_one_the_client_reports(self):
        """The interpreter resolves no model name of its own; the client owns it.

        ``ARGUS_MODEL`` is set here to something different on purpose: it must
        not reach the artifact when a client was injected, because the injected
        client is what actually answered.
        """
        parsed = payload(intents=[intent([parameter("query", "headphones", span=(5, 15))])])
        with mock.patch.dict(os.environ, {"ARGUS_MODEL": "not-the-one-that-answered"}):
            interpreted, _ = run(TEXT, parsed)
        self.assertEqual(interpreted.model, MODEL)

        client = FakeModelClient(
            ModelResult(status="ok", parsed=parsed, model="served-model-2")
        )
        interpreted = interpret(TEXT, "request-test-1", client=client)
        self.assertEqual(interpreted.model, "served-model-2")

    def test_no_client_builds_an_openai_compatible_one_for_the_model(self):
        """``client=None`` is the only path that constructs a real client."""
        parsed = payload(intents=[intent([parameter("query", "headphones", span=(5, 15))])])
        built: list = []

        def factory(model=None):
            built.append(model)
            return FakeModelClient(ModelResult(status="ok", parsed=parsed, model=MODEL))

        with mock.patch("argus.interpreter.OpenAICompatibleClient", factory):
            interpret(TEXT, "request-test-1")
            interpret(TEXT, "request-test-1", model="explicit-model")
        # The model argument is handed straight to the client, which resolves
        # $ARGUS_MODEL itself when it is None.
        self.assertEqual(built, [None, "explicit-model"])

        # And an injected client is never replaced by one.
        with mock.patch("argus.interpreter.OpenAICompatibleClient", factory):
            interpret(
                TEXT,
                "request-test-1",
                client=FakeModelClient(
                    ModelResult(status="ok", parsed=parsed, model=MODEL)
                ),
            )
        self.assertEqual(built, [None, "explicit-model"])

    # -- input guards ----------------------------------------------------- #

    def test_empty_text_is_rejected_before_any_call(self):
        client = FakeModelClient(ModelResult(status="ok", parsed=payload(), model=MODEL))
        for bad in ("", "   "):
            with self.assertRaises(ContractError) as caught:
                interpret(bad, "request-test-1", client=client)
            self.assertEqual(caught.exception.code, "INVALID_INPUT")
        self.assertEqual(client.calls, [])

    def test_missing_request_id_is_rejected(self):
        client = FakeModelClient(ModelResult(status="ok", parsed=payload(), model=MODEL))
        with self.assertRaises(ContractError) as caught:
            interpret(TEXT, "", client=client)
        self.assertEqual(caught.exception.code, "INVALID_INPUT")
        self.assertEqual(client.calls, [])

    def test_empty_interpretation_is_returned_not_repaired(self):
        # Nothing supported in the text: the gate turns this into G1.
        interpreted, _ = run("what is the weather", payload(ambiguities=["no supported site"]))
        self.assertEqual(interpreted.intents, [])
        self.assertEqual(interpreted.ambiguities, ["no supported site"])

    # -- open-world intents ------------------------------------------------ #

    def test_open_intent_reads_the_hostname_out_of_an_explicit_url(self):
        text = "Find remote jobs on https://jobs.example.com/search?q=engineer"
        parsed = payload(
            intents=[
                open_intent(
                    [parameter("query", "remote jobs", span=(5, 16))],
                    target_domain="https://jobs.example.com/search?q=engineer",
                )
            ]
        )
        interpreted, _ = run(text, parsed)

        only = interpreted.intents[0]
        self.assertEqual(only.kind, "open")
        self.assertEqual(only.target_domain, "jobs.example.com")
        self.assertEqual(only.goal, "Find software engineering jobs")
        self.assertEqual(only.expected_record_shape, ["title", "company", "url"])
        self.assertEqual(interpreted.ambiguities, [])
        # An open intent is still the same handoff artifact.
        self.assertEqual(
            InterpretedRequest.from_dict(interpreted.to_dict()), interpreted
        )

    def test_open_intent_without_a_site_has_no_target_domain(self):
        text = "Find remote software engineering jobs"
        parsed = payload(
            intents=[
                open_intent(
                    [parameter("query", "software engineering", span=(12, 32))],
                    site_id="",
                    target_domain=None,
                )
            ]
        )
        interpreted, _ = run(text, parsed)

        self.assertIsNone(interpreted.intents[0].target_domain)
        self.assertEqual(len(interpreted.ambiguities), 1)
        self.assertIn("target domain", interpreted.ambiguities[0])

    def test_open_intent_with_a_malformed_domain_is_dropped(self):
        parsed = payload(
            intents=[open_intent(target_domain="jobs example com/../etc")]
        )
        interpreted, _ = run("Find jobs somewhere", parsed)

        self.assertIsNone(interpreted.intents[0].target_domain)
        self.assertEqual(len(interpreted.ambiguities), 1)
        self.assertIn("not a valid hostname", interpreted.ambiguities[0])

    def test_a_domain_the_policy_blocks_is_kept_for_the_gate(self):
        # The interpreter reports; the gate rejects with the policy's reason.
        for blocked in ("127.0.0.1", "intranet.corp"):
            parsed = payload(intents=[open_intent(target_domain=blocked)])
            interpreted, _ = run("Find jobs on the intranet", parsed)
            self.assertEqual(interpreted.intents[0].target_domain, blocked)
            self.assertEqual(interpreted.ambiguities, [])
            self.assertFalse(registry.domain_allowed(blocked)[0])

    def test_best_10_jobs_keeps_a_vague_rank_and_a_resolved_limit(self):
        text = "Find the best 10 software engineering jobs on jobs.example.com"
        parsed = payload(
            intents=[
                open_intent(
                    [parameter("query", "software engineering", span=(17, 37))],
                    criteria=[
                        criterion("best", "rank", span=(9, 13), confidence=0.4),
                        criterion("10", "limit", parameter=10, span=(14, 16), confidence=0.99),
                    ],
                )
            ]
        )
        interpreted, _ = run(text, parsed)

        rank, limit = interpreted.intents[0].criteria
        self.assertEqual((rank.text, rank.kind, rank.parameter), ("best", "rank", None))
        self.assertEqual(rank.span, (9, 13))
        self.assertEqual(text[9:13], "best")
        self.assertLess(rank.confidence, 0.6)
        self.assertEqual((limit.text, limit.kind, limit.parameter), ("10", "limit", 10))
        self.assertEqual(limit.span, (14, 16))
        self.assertEqual(interpreted.ambiguities, [])

    def test_highest_salary_remote_only_gives_a_rank_and_a_filter(self):
        text = (
            "Find the highest salary 10 software engineering jobs, remote only, "
            "on jobs.example.com"
        )
        parsed = payload(
            intents=[
                open_intent(
                    [parameter("query", "software engineering", span=(27, 47))],
                    criteria=[
                        criterion(
                            "highest salary", "rank", parameter="salary",
                            span=(9, 23), confidence=0.99,
                        ),
                        criterion(
                            "remote only", "filter", parameter="remote",
                            span=(54, 65), confidence=0.97,
                        ),
                    ],
                    expected_record_shape=("title", "company", "url", "salary", "remote"),
                )
            ]
        )
        interpreted, _ = run(text, parsed)

        rank, remote = interpreted.intents[0].criteria
        self.assertEqual((rank.kind, rank.parameter, rank.span), ("rank", "salary", (9, 23)))
        self.assertEqual((remote.kind, remote.parameter, remote.span), ("filter", "remote", (54, 65)))
        # Each criterion cites its own words, and both citations verify.
        self.assertEqual(text[9:23], "highest salary")
        self.assertEqual(text[54:65], "remote only")
        self.assertEqual(rank.confidence, 0.99)
        self.assertEqual(remote.confidence, 0.97)
        self.assertEqual(
            interpreted.intents[0].expected_record_shape,
            ["title", "company", "url", "salary", "remote"],
        )
        self.assertEqual(interpreted.ambiguities, [])

    def test_a_registry_match_is_preferred_over_an_open_intent(self):
        # The prompt asks for a registry match first, and a registry intent
        # carries no open context whatever tier two added.
        self.assertIn('Prefer kind="registry"', system_prompt())
        parsed = payload(
            intents=[
                open_intent(
                    [parameter("query", "headphones", span=(5, 15))],
                    site_id="demo-catalog",
                    operation="search_products",
                    kind="registry",
                    target_domain=None,
                    goal=None,
                    criteria=(),
                    expected_record_shape=(),
                )
            ]
        )
        interpreted, _ = run(TEXT, parsed)

        only = interpreted.intents[0]
        self.assertEqual(only.kind, "registry")
        self.assertEqual(only.operation, "search_products")
        self.assertIsNone(only.target_domain)
        self.assertIsNone(only.goal)
        self.assertEqual(only.criteria, [])
        self.assertEqual(only.expected_record_shape, [])
        self.assertEqual(interpreted.ambiguities, [])

    def test_open_context_on_a_registry_intent_is_dropped_with_a_note(self):
        parsed = payload(
            intents=[
                open_intent(
                    [parameter("query", "headphones", span=(5, 15))],
                    site_id="demo-catalog",
                    operation="search_products",
                    kind="registry",
                    target_domain="demo-catalog.example.com",
                    goal="Find headphones",
                    criteria=[criterion("cheapest", "rank", span=(0, 4))],
                )
            ]
        )
        interpreted, _ = run(TEXT, parsed)

        only = interpreted.intents[0]
        self.assertEqual(only.kind, "registry")
        self.assertIsNone(only.target_domain)
        self.assertEqual(only.criteria, [])
        self.assertEqual(len(interpreted.ambiguities), 1)
        self.assertIn("target_domain", interpreted.ambiguities[0])
        self.assertIn("criteria", interpreted.ambiguities[0])

    def test_criterion_span_that_does_not_match_is_downgraded(self):
        text = "Find the highest salary 10 software engineering jobs"
        parsed = payload(
            intents=[
                open_intent(
                    criteria=[
                        # "Find" is not where "highest salary" came from.
                        criterion(
                            "highest salary", "rank", parameter="salary",
                            span=(0, 4), confidence=0.99,
                        ),
                        criterion("10", "limit", parameter=10, span=(24, 26), confidence=0.99),
                    ]
                )
            ]
        )
        interpreted, _ = run(text, parsed)

        rank, limit = interpreted.intents[0].criteria
        # The criterion is kept - never silently dropped - but unverified.
        self.assertEqual(rank.text, "highest salary")
        self.assertEqual(rank.parameter, "salary")
        self.assertIsNone(rank.span)
        self.assertEqual(rank.confidence, 0.0)
        self.assertEqual(len(interpreted.ambiguities), 1)
        self.assertIn("highest salary", interpreted.ambiguities[0])
        self.assertIn("rank criterion", interpreted.ambiguities[0])
        # The criterion whose span did match is untouched.
        self.assertEqual(limit.span, (24, 26))
        self.assertEqual(limit.confidence, 0.99)

    def test_criterion_without_a_span_is_downgraded(self):
        parsed = payload(
            intents=[open_intent(criteria=[criterion("best", "rank", span=None)])]
        )
        interpreted, _ = run("Find the best jobs on jobs.example.com", parsed)
        rank = interpreted.intents[0].criteria[0]
        self.assertIsNone(rank.span)
        self.assertEqual(rank.confidence, 0.0)
        self.assertIn("no span was given", interpreted.ambiguities[0])

    def test_unsupported_criterion_kind_is_an_extraction_failure(self):
        parsed = payload(
            intents=[open_intent(criteria=[criterion("best", "sort", span=(9, 13))])]
        )
        with self.assertRaises(ContractError) as caught:
            run("Find the best jobs on jobs.example.com", parsed)
        self.assertEqual(caught.exception.code, "EXTRACTION_FAILED")

    def test_unsupported_intent_kind_is_an_extraction_failure(self):
        parsed = payload(intents=[open_intent(kind="freeform")])
        with self.assertRaises(ContractError) as caught:
            run("Find jobs on jobs.example.com", parsed)
        self.assertEqual(caught.exception.code, "EXTRACTION_FAILED")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
