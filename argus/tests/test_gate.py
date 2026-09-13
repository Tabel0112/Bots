"""Tests for stage 2, the acceptance gate.

Every catalog rule (G1 to G7) and every open-world rule (S1 to S5) gets a
fixture-shaped :class:`InterpretedRequest`, and each rule's precedence over the
next one is tested, not just the rule alone.  The shipped ``argus/examples``
fixtures are gated too: the registry request is accepted, the open request
clarifies on S4 with the approved question, and the four gate fixtures must
agree with the rules in :mod:`argus.gate`.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from argus import registry
from argus.contracts import (
    Criterion,
    GateDecision,
    Intent,
    InterpretedRequest,
    MissingParameter,
    ParameterOrigin,
)
from argus.gate import CONFIDENCE_FLOOR, RANK_QUESTION, gate

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"
TEXT = "Find headphones under $150 in the demo catalog"
OPEN_TEXT = "Find the best 10 software engineering jobs on jobs.example.com"
SALARY_TEXT = (
    "Find the highest salary 10 software engineering jobs, remote only, "
    "on jobs.example.com"
)
#: The clarification docs/hackathon/ARGUS.md approved, word for word.
BEST_QUESTION = (
    "What should 'best' mean? For example, lowest price, highest rating, or newest."
)


def origin(value, *, source="text_span", confidence=0.95, span=None):
    return ParameterOrigin(value=value, source=source, confidence=confidence, span=span)


def intent(
    parameters, *, site_id="demo-catalog", operation="search_products", confidence=0.94
):
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


def criterion(text, kind, *, parameter=None, span=None, confidence=0.95):
    return Criterion(
        text=text, kind=kind, parameter=parameter, span=span, confidence=confidence
    )


def open_intent(
    *,
    site_id="jobs.example.com",
    operation="find_jobs",
    parameters=None,
    confidence=0.9,
    target_domain="jobs.example.com",
    goal="Find software engineering jobs",
    criteria=(),
    expected_record_shape=("title", "company", "url"),
):
    """An accepted-by-default open intent; each S test breaks one thing."""
    return Intent(
        site_id=site_id,
        operation=operation,
        parameters=parameters if parameters is not None else {},
        confidence=confidence,
        kind="open",
        target_domain=target_domain,
        goal=goal,
        criteria=list(criteria),
        expected_record_shape=list(expected_record_shape),
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
        decision = gate(
            interpreted([good_intent(), good_intent(site_id="example-shop")])
        )
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
                [
                    intent(
                        {"max_price": origin(20, source="structured", confidence=0.9)}
                    )
                ],
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
                        parameters={
                            "max_price": origin(
                                "cheap", confidence=0.9, span=None, source="structured"
                            )
                        }
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
                [
                    good_intent(
                        parameters={
                            "max_price": origin(-10, confidence=0.9, span=(22, 26))
                        }
                    )
                ]
            )
        )
        self.assertDecision(decision, "reject", "G5")
        self.assertIn("at least 0", decision.reason)

    def test_g5_fixed_value_may_not_be_changed(self):
        decision = gate(
            interpreted(
                [
                    good_intent(
                        parameters={
                            "currency": origin(
                                "CAD", source="structured", confidence=0.9
                            )
                        }
                    )
                ]
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
                            "max_price": origin(
                                "cheap", source="structured", confidence=0.9
                            ),
                            "colour": origin(
                                "red", source="structured", confidence=0.9
                            ),
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
                [
                    good_intent(
                        parameters={
                            "colour": origin("red", source="structured", confidence=0.9)
                        }
                    )
                ]
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
                            "colour": origin(
                                "red", source="structured", confidence=0.9
                            ),
                            "in_stock": origin(
                                True, source="structured", confidence=0.9
                            ),
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
                [
                    good_intent(
                        parameters={
                            "query": origin("headphones", confidence=0.3, span=(5, 15))
                        }
                    )
                ],
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
                            "query": origin(
                                "headphones", source="structured", confidence=0.0
                            )
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
                [
                    good_intent(
                        parameters={
                            "max_price": origin(150, confidence=0.1, span=(22, 26))
                        }
                    )
                ]
            )
        )
        self.assertDecision(decision, "accept", "G0")

    def test_g7_threshold_is_inclusive_at_the_floor(self):
        decision = gate(
            interpreted(
                [
                    good_intent(
                        parameters={
                            "query": origin(
                                "headphones", confidence=CONFIDENCE_FLOOR, span=(5, 15)
                            )
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
                            for name, value in registry.defaults(
                                "search_products"
                            ).items()
                        }
                    )
                ]
            )
        )
        self.assertDecision(decision, "accept", "G0")

    def test_ambiguities_alone_do_not_block_a_confident_request(self):
        decision = gate(
            interpreted(
                [good_intent()], ambiguities=["the catalog may list refurbished units"]
            )
        )
        self.assertDecision(decision, "accept", "G0")

    # -- shipped fixtures --------------------------------------------------- #

    def test_example_interpreted_request_is_accepted(self):
        data = json.loads(
            (EXAMPLES / "interpreted_request.json").read_text(encoding="utf-8")
        )
        decision = gate(InterpretedRequest.from_dict(data))
        self.assertDecision(decision, "accept", "G0")

    def test_example_gate_clarify_matches_the_rules(self):
        expected = GateDecision.from_dict(
            json.loads((EXAMPLES / "gate_clarify.json").read_text(encoding="utf-8"))
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
            json.loads((EXAMPLES / "gate_reject.json").read_text(encoding="utf-8"))
        )
        decision = gate(interpreted([good_intent(site_id="example-shop")]))
        self.assertEqual(decision.decision, expected.decision)
        self.assertEqual(decision.rule_id, expected.rule_id)
        self.assertEqual(decision.questions, expected.questions)
        self.assertIn("example-shop", decision.reason)

    # -- S1: no target domain ---------------------------------------------- #

    def test_s1_open_intent_without_a_target_domain(self):
        decision = gate(
            interpreted(
                [open_intent(site_id="", target_domain=None)],
                raw_text="Find remote software engineering jobs",
            )
        )
        self.assertDecision(decision, "clarify", "S1")
        self.assertEqual(len(decision.questions), 1)
        self.assertIn("Which site", decision.questions[0])

    # -- S2: the domain policy --------------------------------------------- #

    def test_s2_rejects_a_private_address(self):
        decision = gate(
            interpreted(
                [open_intent(target_domain="127.0.0.1")],
                raw_text="Find jobs on 127.0.0.1",
            )
        )
        self.assertDecision(decision, "reject", "S2")
        self.assertIn("DOMAIN_NOT_ALLOWED", decision.reason)
        # The policy's own reason, not just the verdict.
        self.assertIn("non-public IP address", decision.reason)
        self.assertEqual(decision.questions, [])

    def test_s2_rejects_a_non_public_hostname(self):
        decision = gate(
            interpreted(
                [open_intent(target_domain="intranet.corp")],
                raw_text="Find jobs on intranet.corp",
            )
        )
        self.assertDecision(decision, "reject", "S2")
        self.assertIn("DOMAIN_NOT_ALLOWED", decision.reason)
        self.assertIn("non-public hostname", decision.reason)

    def test_s2_rejects_a_blocklisted_login_host(self):
        decision = gate(
            interpreted(
                [open_intent(target_domain="accounts.google.com")],
                raw_text="Read the sign-in options on accounts.google.com",
            )
        )
        self.assertDecision(decision, "reject", "S2")
        self.assertIn("DOMAIN_NOT_ALLOWED", decision.reason)

    def test_s2_runs_after_s1(self):
        # An intent with neither a domain nor a goal is asked about, not rejected.
        decision = gate(
            interpreted(
                [open_intent(target_domain=None, goal=None)],
                raw_text="Find something somewhere",
            )
        )
        self.assertDecision(decision, "clarify", "S1")

    # -- S3: no goal -------------------------------------------------------- #

    def test_s3_open_intent_without_a_goal(self):
        for goal in (None, "   "):
            decision = gate(
                interpreted([open_intent(goal=goal)], raw_text="jobs.example.com")
            )
            self.assertDecision(decision, "clarify", "S3")
            self.assertEqual(len(decision.questions), 1)
            self.assertIn("jobs.example.com", decision.questions[0])

    def test_s3_runs_after_s2(self):
        decision = gate(
            interpreted(
                [open_intent(target_domain="127.0.0.1", goal=None)],
                raw_text="Find something on 127.0.0.1",
            )
        )
        self.assertDecision(decision, "reject", "S2")

    # -- S4: a vague ranking ------------------------------------------------ #

    def test_s4_vague_rank_asks_the_approved_question(self):
        decision = gate(
            interpreted(
                [
                    open_intent(
                        criteria=[
                            criterion("best", "rank", span=(9, 13), confidence=0.4),
                            criterion(
                                "10",
                                "limit",
                                parameter=10,
                                span=(14, 16),
                                confidence=0.99,
                            ),
                        ]
                    )
                ],
                raw_text=OPEN_TEXT,
            )
        )
        self.assertDecision(decision, "clarify", "S4")
        self.assertEqual(decision.questions, [BEST_QUESTION])
        self.assertIn("best", decision.reason)

    def test_s4_uses_the_users_own_ranking_words(self):
        decision = gate(
            interpreted(
                [
                    open_intent(
                        criteria=[criterion("most exciting", "rank", confidence=0.2)]
                    )
                ],
                raw_text="Find the most exciting jobs on jobs.example.com",
            )
        )
        self.assertDecision(decision, "clarify", "S4")
        self.assertEqual(
            decision.questions, [RANK_QUESTION.format(text="most exciting")]
        )
        self.assertIn("most exciting", decision.questions[0])

    def test_s4_threshold_is_inclusive_at_the_floor(self):
        decision = gate(
            interpreted(
                [
                    open_intent(
                        criteria=[
                            criterion(
                                "best",
                                "rank",
                                span=(9, 13),
                                confidence=CONFIDENCE_FLOOR,
                            )
                        ]
                    )
                ],
                raw_text=OPEN_TEXT,
            )
        )
        self.assertDecision(decision, "accept", "S0")

    def test_s4_ignores_a_filter_or_limit_criterion(self):
        decision = gate(
            interpreted(
                [
                    open_intent(
                        criteria=[
                            criterion(
                                "remote only",
                                "filter",
                                parameter="remote",
                                confidence=0.2,
                            ),
                            criterion("10", "limit", parameter=10, confidence=0.1),
                        ]
                    )
                ],
                raw_text=SALARY_TEXT,
            )
        )
        self.assertDecision(decision, "accept", "S0")

    def test_s5_runs_before_s4(self):
        decision = gate(
            interpreted(
                [open_intent(criteria=[criterion("best", "rank", confidence=0.3)])],
                raw_text="Log in to jobs.example.com and find the best jobs",
            )
        )
        self.assertDecision(decision, "reject", "S5")

    def test_s5_runs_before_s1_when_no_site_is_named(self):
        decision = gate(
            interpreted(
                [
                    open_intent(
                        target_domain=None,
                        goal="Log in to the user's bank and download their statements.",
                    )
                ],
                raw_text="Log in to my bank and download my statements",
            )
        )
        self.assertDecision(decision, "reject", "S5")

    def test_s5_catches_book_me_a_table(self):
        decision = gate(
            interpreted(
                [open_intent(target_domain=None, goal="Book a table")],
                raw_text="Book me a table for two tonight",
            )
        )
        self.assertDecision(decision, "reject", "S5")

    # -- S5: actions a read-only run never performs ------------------------- #

    def test_s5_rejects_a_login_and_download_request(self):
        decision = gate(
            interpreted(
                [
                    open_intent(
                        target_domain="shop.example.com", goal="Download my invoices"
                    )
                ],
                raw_text="Log in to shop.example.com and download my invoices",
            )
        )
        self.assertDecision(decision, "reject", "S5")
        self.assertIn("ACTION_CLASS_NOT_ALLOWED", decision.reason)
        self.assertIn("login", decision.reason)
        self.assertEqual(decision.questions, [])

    def test_s5_accepts_a_documentation_request_about_the_same_action(self):
        decision = gate(
            interpreted(
                [
                    open_intent(
                        target_domain="shop.example.com",
                        goal="Read the documentation explaining how to log in",
                        expected_record_shape=("title", "url", "steps"),
                    )
                ],
                raw_text="How do I log in to shop.example.com?",
            )
        )
        self.assertDecision(decision, "accept", "S0")

    def test_s5_reads_the_goal_as_well_as_the_request(self):
        decision = gate(
            interpreted(
                [
                    open_intent(
                        target_domain="shop.example.com", goal="Buy the cheapest laptop"
                    )
                ],
                raw_text="Get me the cheapest laptop from shop.example.com",
            )
        )
        self.assertDecision(decision, "reject", "S5")
        self.assertIn("purchase", decision.reason)
        self.assertIn("goal", decision.reason)

    def test_s5_rejects_a_form_submission(self):
        decision = gate(
            interpreted(
                [open_intent(goal="Submit my application form")],
                raw_text="Submit my application form on jobs.example.com",
            )
        )
        self.assertDecision(decision, "reject", "S5")
        self.assertIn("form submission", decision.reason)

    def test_s5_rejects_a_payment_and_a_checkout(self):
        for goal, raw_text in (
            (
                "Pay the outstanding invoice",
                "Pay the outstanding invoice on billing.example.com",
            ),
            (
                "Complete the purchase",
                "Add the laptop to my cart and complete the purchase",
            ),
        ):
            decision = gate(
                interpreted(
                    [open_intent(target_domain="shop.example.com", goal=goal)],
                    raw_text=raw_text,
                )
            )
            self.assertDecision(decision, "reject", "S5")

    def test_s5_leaves_an_ordinary_read_only_request_alone(self):
        for raw_text in (
            SALARY_TEXT,
            "Find the highest paying remote jobs on jobs.example.com",
            "What are the payment options on shop.example.com?",
            "Summarise the checkout instructions on shop.example.com",
        ):
            decision = gate(interpreted([open_intent()], raw_text=raw_text))
            self.assertDecision(decision, "accept", "S0")

    # -- S0 and mixed requests ---------------------------------------------- #

    def test_s0_accepts_a_grounded_open_request(self):
        decision = gate(
            interpreted(
                [
                    open_intent(
                        parameters={
                            "query": origin("software engineering", span=(27, 47))
                        },
                        criteria=[
                            criterion(
                                "highest salary",
                                "rank",
                                parameter="salary",
                                span=(9, 23),
                                confidence=0.99,
                            ),
                            criterion(
                                "remote only",
                                "filter",
                                parameter="remote",
                                span=(54, 65),
                                confidence=0.97,
                            ),
                        ],
                        expected_record_shape=(
                            "title",
                            "company",
                            "url",
                            "salary",
                            "remote",
                        ),
                    )
                ],
                raw_text=SALARY_TEXT,
            )
        )
        self.assertDecision(decision, "accept", "S0")
        self.assertIn("jobs.example.com", decision.reason)
        self.assertEqual(decision.questions, [])

    def test_catalog_rules_run_before_the_open_rules(self):
        decision = gate(
            interpreted(
                [good_intent(site_id="example-shop"), open_intent(target_domain=None)],
                raw_text="Find headphones on example-shop and jobs somewhere",
            )
        )
        self.assertDecision(decision, "reject", "G2")

    def test_a_mixed_request_can_be_accepted(self):
        decision = gate(interpreted([good_intent(), open_intent()], raw_text=TEXT))
        self.assertDecision(decision, "accept", "S0")
        self.assertIn("search_products", decision.reason)
        self.assertIn("jobs.example.com", decision.reason)

    def test_an_open_intent_is_not_judged_by_the_catalog_rules(self):
        # G2/G3 would reject this site and operation; the S rules own it now.
        self.assertNotIn("jobs.example.com", registry.SITES)
        self.assertNotIn("find_jobs", registry.OPERATIONS)
        decision = gate(interpreted([open_intent()], raw_text=OPEN_TEXT))
        self.assertDecision(decision, "accept", "S0")

    # -- shipped open fixtures ---------------------------------------------- #

    def test_example_open_salary_request_matches_the_accept_fixture(self):
        expected = GateDecision.from_dict(
            json.loads((EXAMPLES / "gate_open_accept.json").read_text(encoding="utf-8"))
        )
        data = json.loads(
            (EXAMPLES / "interpreted_request_open_salary.json").read_text(
                encoding="utf-8"
            )
        )
        decision = gate(InterpretedRequest.from_dict(data))
        self.assertEqual(decision.decision, expected.decision)
        self.assertEqual(decision.rule_id, expected.rule_id)
        self.assertEqual(decision.questions, expected.questions)

    def test_example_open_request_clarifies_on_s4(self):
        data = json.loads(
            (EXAMPLES / "interpreted_request_open.json").read_text(encoding="utf-8")
        )
        decision = gate(InterpretedRequest.from_dict(data))
        self.assertDecision(decision, "clarify", "S4")
        self.assertEqual(decision.questions, [BEST_QUESTION])

    def test_example_gate_open_reject_domain_matches_the_rules(self):
        expected = GateDecision.from_dict(
            json.loads(
                (EXAMPLES / "gate_open_reject_domain.json").read_text(encoding="utf-8")
            )
        )
        data = json.loads(
            (EXAMPLES / "interpreted_request_open_salary.json").read_text(
                encoding="utf-8"
            )
        )
        data["intents"][0]["target_domain"] = "127.0.0.1"
        decision = gate(InterpretedRequest.from_dict(data))
        self.assertEqual(decision.decision, expected.decision)
        self.assertEqual(decision.rule_id, expected.rule_id)
        self.assertEqual(decision.questions, expected.questions)
        self.assertIn("127.0.0.1", decision.reason)
        self.assertIn("DOMAIN_NOT_ALLOWED", decision.reason)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
