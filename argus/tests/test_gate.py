"""Tests for stage 2, the acceptance gate.

Every rule gets a fixture-shaped :class:`InterpretedRequest`, plus the shipped
``argus/examples`` fixtures: the interpreted request must be accepted and the
two gate fixtures must agree with the rules in :mod:`argus.gate`.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from argus import registry
from argus.contracts import (
    GateDecision,
    Intent,
    InterpretedRequest,
    MissingParameter,
    ParameterOrigin,
)
from argus.gate import CONFIDENCE_FLOOR, gate

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"
TEXT = "Find headphones under $150 in the demo catalog"


def origin(value, *, source="text_span", confidence=0.95, span=None):
    return ParameterOrigin(
        value=value, source=source, confidence=confidence, span=span
    )


def intent(parameters, *, site_id="demo-catalog", operation="search_products", confidence=0.94):
    return Intent(
        site_id=site_id,
        operation=operation,
        parameters=parameters,
        confidence=confidence,
    )


def interpreted(intents, *, missing_required=None, ambiguities=None, raw_text=TEXT):
    return InterpretedRequest(
        request_id="request-test-1",
        raw_text=raw_text,
        intents=list(intents),
        model="claude-opus-5",
        interpreted_at="2026-09-12T17:00:00Z",
        missing_required=list(missing_required or []),
        ambiguities=list(ambiguities or []),
    )


def good_intent(**overrides):
    parameters = {
        "query": origin("headphones", confidence=0.96, span=(5, 15)),
        "max_price": origin(150, confidence=0.92, span=(22, 26)),
    }
    parameters.update(overrides.pop("parameters", {}))
    return intent(parameters, **overrides)


class GateTest(unittest.TestCase):
    def assertDecision(self, decision, expected, rule_id):
        self.assertIsInstance(decision, GateDecision)
        self.assertEqual(decision.decision, expected)
        self.assertEqual(decision.rule_id, rule_id)
        self.assertTrue(decision.reason)
        # A decision is a message like any other.
        self.assertEqual(GateDecision.from_dict(decision.to_dict()), decision)
        return decision

    # -- G1 ---------------------------------------------------------------- #

    def test_g1_no_intents(self):
        decision = gate(interpreted([], raw_text="what is the weather"))
        self.assertDecision(decision, "reject", "G1")
        self.assertEqual(decision.questions, [])
        self.assertIn("demo-catalog", decision.reason)

    # -- G2 ---------------------------------------------------------------- #

    def test_g2_unknown_site(self):
        decision = gate(interpreted([good_intent(site_id="example-shop")]))
        self.assertDecision(decision, "reject", "G2")
        self.assertIn("example-shop", decision.reason)

    def test_g2_fires_for_any_intent_of_a_compound_request(self):
        decision = gate(interpreted([good_intent(), good_intent(site_id="example-shop")]))
        self.assertDecision(decision, "reject", "G2")

    # -- G3 ---------------------------------------------------------------- #

    def test_g3_unsupported_operation(self):
        decision = gate(
            interpreted(
                [
                    intent(
                        {"query": origin("headphones", span=(5, 15))},
                        operation="buy_product",
                    )
                ]
            )
        )
        self.assertDecision(decision, "reject", "G3")
        self.assertIn("buy_product", decision.reason)
        self.assertIn("search_products", decision.reason)

    # -- G4 ---------------------------------------------------------------- #

    def test_g4_missing_required_parameter_uses_the_interpreters_question(self):
        question = "What product should I search the demo catalog for?"
        decision = gate(
            interpreted(
                [intent({"max_price": origin(20, source="structured", confidence=0.9)})],
                missing_required=[MissingParameter(0, "query", question)],
            )
        )
        self.assertDecision(decision, "clarify", "G4")
        self.assertEqual(decision.questions, [question])
        self.assertIn("query", decision.reason)

    def test_g4_falls_back_to_its_own_question(self):
        decision = gate(interpreted([intent({})]))
        self.assertDecision(decision, "clarify", "G4")
        self.assertEqual(len(decision.questions), 1)
        self.assertIn("query", decision.questions[0])

    def test_g4_asks_once_per_missing_parameter_across_intents(self):
        question = "What product should I search the demo catalog for?"
        decision = gate(
            interpreted(
                [intent({}), intent({})],
                missing_required=[
                    MissingParameter(0, "query", question),
                    MissingParameter(1, "query", question),
                ],
            )
        )
        self.assertDecision(decision, "clarify", "G4")
        self.assertEqual(decision.questions, [question])

    # -- G5 ---------------------------------------------------------------- #

    def test_g5_wrong_type(self):
        decision = gate(
            interpreted(
                [
                    good_intent(
                        parameters={"max_price": origin("cheap", confidence=0.9, span=None, source="structured")}
                    )
                ]
            )
        )
        self.assertDecision(decision, "reject", "G5")
        self.assertIn("max_price", decision.reason)
        self.assertIn("number", decision.reason)

    def test_g5_out_of_scope_value(self):
        decision = gate(
            interpreted(
                [good_intent(parameters={"max_price": origin(-10, confidence=0.9, span=(22, 26))})]
            )
        )
        self.assertDecision(decision, "reject", "G5")
        self.assertIn("at least 0", decision.reason)

    def test_g5_fixed_value_may_not_be_changed(self):
        decision = gate(
            interpreted(
                [good_intent(parameters={"currency": origin("CAD", source="structured", confidence=0.9)})]
            )
        )
        self.assertDecision(decision, "reject", "G5")
        self.assertIn("currency", decision.reason)

    def test_g5_runs_before_g6(self):
        # A request that is wrong in both ways is reported by the earlier rule.
        decision = gate(
            interpreted(
                [
                    good_intent(
                        parameters={
                            "max_price": origin("cheap", source="structured", confidence=0.9),
                            "colour": origin("red", source="structured", confidence=0.9),
                        }
                    )
                ]
            )
        )
        self.assertDecision(decision, "reject", "G5")

    # -- G6 ---------------------------------------------------------------- #

    def test_g6_unknown_parameter_is_never_dropped_silently(self):
        decision = gate(
            interpreted(
                [good_intent(parameters={"colour": origin("red", source="structured", confidence=0.9)})]
            )
        )
        self.assertDecision(decision, "reject", "G6")
        self.assertIn("colour", decision.reason)
        self.assertIn("query", decision.reason)

    def test_g6_lists_every_unknown_parameter(self):
        decision = gate(
            interpreted(
                [
                    good_intent(
                        parameters={
                            "colour": origin("red", source="structured", confidence=0.9),
                            "in_stock": origin(True, source="structured", confidence=0.9),
                        }
                    )
                ]
            )
        )
        self.assertDecision(decision, "reject", "G6")
        self.assertIn("colour", decision.reason)
        self.assertIn("in_stock", decision.reason)

    # -- G7 ---------------------------------------------------------------- #

    def test_g7_low_confidence_required_parameter(self):
        decision = gate(
            interpreted(
                [good_intent(parameters={"query": origin("headphones", confidence=0.3, span=(5, 15))})],
                ambiguities=["'headphones' might have been 'earphones'."],
            )
        )
        self.assertDecision(decision, "clarify", "G7")
        self.assertEqual(len(decision.questions), 1)
        self.assertIn("headphones", decision.questions[0])
        self.assertIn("earphones", decision.reason)

    def test_g7_fires_on_a_dropped_span(self):
        # What the interpreter does to a fabricated citation: confidence 0.
        decision = gate(
            interpreted(
                [
                    good_intent(
                        parameters={
                            "query": origin("headphones", source="structured", confidence=0.0)
                        }
                    )
                ],
                ambiguities=["the citation for 'query' did not match the request"],
            )
        )
        self.assertDecision(decision, "clarify", "G7")

    def test_g7_ignores_an_optional_parameter(self):
        decision = gate(
            interpreted(
                [good_intent(parameters={"max_price": origin(150, confidence=0.1, span=(22, 26))})]
            )
        )
        self.assertDecision(decision, "accept", "G0")

    def test_g7_threshold_is_inclusive_at_the_floor(self):
        decision = gate(
            interpreted(
                [
                    good_intent(
                        parameters={
                            "query": origin("headphones", confidence=CONFIDENCE_FLOOR, span=(5, 15))
                        }
                    )
                ]
            )
        )
        self.assertDecision(decision, "accept", "G0")

    # -- G0 ---------------------------------------------------------------- #

    def test_g0_accept(self):
        decision = gate(interpreted([good_intent()]))
        self.assertDecision(decision, "accept", "G0")
        self.assertEqual(decision.questions, [])
        self.assertIn("search_products", decision.reason)

    def test_g0_accepts_a_compound_request(self):
        second = intent({"query": origin("keyboards", confidence=0.88, span=(5, 15))})
        decision = gate(interpreted([good_intent(), second]))
        self.assertDecision(decision, "accept", "G0")

    def test_g0_accepts_the_registry_defaults(self):
        decision = gate(
            interpreted(
                [
                    good_intent(
                        parameters={
                            name: origin(value, source="default", confidence=1.0)
                            for name, value in registry.defaults("search_products").items()
                        }
                    )
                ]
            )
        )
        self.assertDecision(decision, "accept", "G0")

    def test_ambiguities_alone_do_not_block_a_confident_request(self):
        decision = gate(
            interpreted([good_intent()], ambiguities=["the catalog may list refurbished units"])
        )
        self.assertDecision(decision, "accept", "G0")

    # -- shipped fixtures --------------------------------------------------- #

    def test_example_interpreted_request_is_accepted(self):
        data = json.loads((EXAMPLES / "interpreted_request.json").read_text())
        decision = gate(InterpretedRequest.from_dict(data))
        self.assertDecision(decision, "accept", "G0")

    def test_example_gate_clarify_matches_the_rules(self):
        expected = GateDecision.from_dict(
            json.loads((EXAMPLES / "gate_clarify.json").read_text())
        )
        decision = gate(
            interpreted(
                [intent({})],
                missing_required=[MissingParameter(0, "query", expected.questions[0])],
                raw_text="Find something in the demo catalog",
            )
        )
        self.assertEqual(decision.decision, expected.decision)
        self.assertEqual(decision.rule_id, expected.rule_id)
        self.assertEqual(decision.questions, expected.questions)

    def test_example_gate_reject_matches_the_rules(self):
        expected = GateDecision.from_dict(
            json.loads((EXAMPLES / "gate_reject.json").read_text())
        )
        decision = gate(interpreted([good_intent(site_id="example-shop")]))
        self.assertEqual(decision.decision, expected.decision)
        self.assertEqual(decision.rule_id, expected.rule_id)
        self.assertEqual(decision.questions, expected.questions)
        self.assertIn("example-shop", decision.reason)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
