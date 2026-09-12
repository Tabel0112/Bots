"""Tests for stage 1, the interpreter.

No test here touches the network.  Every test injects a fake client whose
``messages.parse`` returns a canned response object with ``parsed_output`` and
``stop_reason``, so the module's own behaviour - span verification, the refusal
check, model resolution and the conversion to the dataclasses - is what is under
test, not the model's judgement.
"""

from __future__ import annotations

import os
import unittest
from types import SimpleNamespace
from unittest import mock

from argus import registry
from argus.contracts import ContractError, InterpretedRequest
from argus.interpreter import DEFAULT_MODEL, MAX_TOKENS, interpret, system_prompt

TEXT = "Find headphones under $150 in the demo catalog"
COMPOUND = "Find headphones under $150 and also search for keyboards"


# --------------------------------------------------------------------------- #
# Fake client
# --------------------------------------------------------------------------- #


class _FakeMessages:
    """Stands in for ``client.messages``; records the request it was given."""

    def __init__(self, response):
        self._response = response
        self.calls: list[dict] = []

    def parse(self, **kwargs):
        self.calls.append(kwargs)
        return self._response


class FakeClient:
    """The injected client: ``client.messages.parse`` returns a canned answer."""

    def __init__(self, response):
        self.messages = _FakeMessages(response)


class Response(SimpleNamespace):
    """A canned ``messages.parse`` result."""

    def __init__(self, parsed_output=None, stop_reason="end_turn", stop_details=None):
        super().__init__(
            parsed_output=parsed_output,
            stop_reason=stop_reason,
            stop_details=stop_details,
        )


class RefusedResponse:
    """A refusal whose content explodes if anything reads it before the check."""

    stop_reason = "refusal"
    stop_details = SimpleNamespace(category="cyber", explanation="declined")

    @property
    def parsed_output(self):  # pragma: no cover - the test asserts it is not hit
        raise AssertionError("content was read before stop_reason was checked")


def parameter(name, value, *, source="text_span", confidence=0.95, span=None):
    start, end = span if span else (None, None)
    return SimpleNamespace(
        name=name,
        value=value,
        source=source,
        confidence=confidence,
        span_start=start,
        span_end=end,
    )


def intent(parameters, *, site_id="demo-catalog", operation="search_products", confidence=0.94):
    return SimpleNamespace(
        site_id=site_id,
        operation=operation,
        parameters=list(parameters),
        confidence=confidence,
    )


def missing(intent_index, parameter_name, question):
    return SimpleNamespace(
        intent_index=intent_index, parameter=parameter_name, question=question
    )


def payload(intents=(), missing_required=(), ambiguities=()):
    return SimpleNamespace(
        intents=list(intents),
        missing_required=list(missing_required),
        ambiguities=list(ambiguities),
    )


def run(text, parsed, *, request_id="request-test-1", model=None, stop_reason="end_turn"):
    client = FakeClient(Response(parsed_output=parsed, stop_reason=stop_reason))
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
        self.assertEqual(interpreted.model, DEFAULT_MODEL)
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
        self.assertEqual(len(client.messages.calls), 1)
        call = client.messages.calls[0]

        self.assertEqual(call["model"], DEFAULT_MODEL)
        self.assertEqual(call["max_tokens"], MAX_TOKENS)
        self.assertEqual(MAX_TOKENS, 4096)
        self.assertIn("output_format", call)
        self.assertNotIn("temperature", call)
        # One user message, no assistant prefill.
        self.assertEqual(call["messages"], [{"role": "user", "content": TEXT}])
        self.assertIn("search_products", call["system"])

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

    def test_refusal_raises_model_refused_without_reading_content(self):
        client = FakeClient(RefusedResponse())
        with self.assertRaises(ContractError) as caught:
            interpret(TEXT, "request-test-1", client=client)
        self.assertEqual(caught.exception.code, "MODEL_REFUSED")
        self.assertEqual(caught.exception.typed_error.code, "MODEL_REFUSED")
        self.assertIn("cyber", str(caught.exception))

    def test_truncated_response_is_an_extraction_failure(self):
        parsed = payload(intents=[intent([parameter("query", "headphones", span=(5, 15))])])
        with self.assertRaises(ContractError) as caught:
            run(TEXT, parsed, stop_reason="max_tokens")
        self.assertEqual(caught.exception.code, "EXTRACTION_FAILED")

    def test_no_parsed_output_is_an_extraction_failure(self):
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

    # -- model resolution ------------------------------------------------- #

    def test_model_resolution_order(self):
        parsed = payload(intents=[intent([parameter("query", "headphones", span=(5, 15))])])

        interpreted, _ = run(TEXT, parsed)
        self.assertEqual(interpreted.model, DEFAULT_MODEL)

        with mock.patch.dict(os.environ, {"ARGUS_MODEL": "claude-opus-5-from-env"}):
            interpreted, client = run(TEXT, parsed)
            self.assertEqual(interpreted.model, "claude-opus-5-from-env")
            self.assertEqual(client.messages.calls[0]["model"], "claude-opus-5-from-env")

            interpreted, client = run(TEXT, parsed, model="claude-opus-5-explicit")
            self.assertEqual(interpreted.model, "claude-opus-5-explicit")
            self.assertEqual(client.messages.calls[0]["model"], "claude-opus-5-explicit")

    # -- input guards ----------------------------------------------------- #

    def test_empty_text_is_rejected_before_any_call(self):
        client = FakeClient(Response(parsed_output=payload()))
        for bad in ("", "   "):
            with self.assertRaises(ContractError) as caught:
                interpret(bad, "request-test-1", client=client)
            self.assertEqual(caught.exception.code, "INVALID_INPUT")
        self.assertEqual(client.messages.calls, [])

    def test_missing_request_id_is_rejected(self):
        client = FakeClient(Response(parsed_output=payload()))
        with self.assertRaises(ContractError) as caught:
            interpret(TEXT, "", client=client)
        self.assertEqual(caught.exception.code, "INVALID_INPUT")

    def test_empty_interpretation_is_returned_not_repaired(self):
        # Nothing supported in the text: the gate turns this into G1.
        interpreted, _ = run("what is the weather", payload(ambiguities=["no supported site"]))
        self.assertEqual(interpreted.intents, [])
        self.assertEqual(interpreted.ambiguities, ["no supported site"])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
