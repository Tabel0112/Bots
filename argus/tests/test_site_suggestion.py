"""Offline tests for model-suggested open-world sites."""

from __future__ import annotations

import unittest

from argus.contracts import Intent, InterpretedRequest, ParameterOrigin
from argus.fakes import FakeModelClient
from argus.model_client import ModelResult
from argus.site_suggestion import suggest_sites


def request(domain=None) -> InterpretedRequest:
    return InterpretedRequest(
        request_id="request-open",
        raw_text="Give me the best condos in Toronto",
        intents=[
            Intent(
                site_id=f"open:{domain}" if domain else "",
                operation="search",
                parameters={
                    "query": ParameterOrigin("Toronto condos", "structured", 0.95)
                },
                confidence=0.9,
                kind="open",
                target_domain=domain,
                goal="Find the best condos in Toronto",
                expected_record_shape=["title", "url", "price"],
            )
        ],
        model="fake-interpreter",
        interpreted_at="2026-09-13T00:00:00Z",
        ambiguities=["intent 0: Which site should ARGUS search?"],
    )


def suggestions(*candidates) -> ModelResult:
    return ModelResult(
        status="ok",
        parsed={"candidates": list(candidates)},
        model="fake-suggester",
    )


class SiteSuggestionTests(unittest.TestCase):
    def test_allowed_candidate_is_chosen_and_recorded(self):
        client = FakeModelClient(
            suggestions(
                {
                    "domain": "realtor.ca",
                    "reason": "Public real-estate listings",
                    "confidence": 0.91,
                }
            )
        )
        original = request()
        result = suggest_sites(original, client)
        intent = result.intents[0]
        self.assertEqual(intent.target_domain, "realtor.ca")
        self.assertEqual(intent.site_id, "open:realtor.ca")
        self.assertEqual(intent.parameters["site_choice"].value, "realtor.ca")
        self.assertEqual(intent.parameters["site_choice"].source, "suggested")
        self.assertEqual(intent.parameters["site_choice"].confidence, 0.91)
        self.assertIsNone(intent.parameters["site_choice"].span)
        self.assertEqual(result.ambiguities, [])
        self.assertIsNone(original.intents[0].target_domain)
        self.assertEqual(len(client.calls), 1)
        self.assertEqual(client.calls[0]["max_tokens"], 400)

    def test_blocked_candidate_is_skipped_for_first_allowed_candidate(self):
        client = FakeModelClient(
            suggestions(
                {"domain": "127.0.0.1", "reason": "Private", "confidence": 0.9},
                {
                    "domain": "example.com",
                    "reason": "Public",
                    "confidence": 0.7,
                },
            )
        )
        result = suggest_sites(request(), client)
        self.assertEqual(result.intents[0].target_domain, "example.com")

    def test_refusal_leaves_request_untouched(self):
        client = FakeModelClient(ModelResult(status="refusal", model="fake"))
        original = request()
        result = suggest_sites(original, client)
        self.assertEqual(result, original)
        self.assertEqual(len(client.calls), 1)

    def test_existing_domain_makes_no_model_call(self):
        client = FakeModelClient([])
        original = request("example.com")
        result = suggest_sites(original, client)
        self.assertEqual(result, original)
        self.assertEqual(client.calls, [])

    def test_suggested_parameter_origin_round_trips(self):
        origin = ParameterOrigin("example.com", "suggested", 0.8)
        self.assertEqual(ParameterOrigin.from_dict(origin.to_dict()), origin)


if __name__ == "__main__":
    unittest.main()
