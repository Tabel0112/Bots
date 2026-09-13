"""Tests for the model-call boundary, :mod:`argus.model_client`.

No test here touches the network.  :class:`OpenAICompatibleClient` is driven
through a fake SDK object injected with the private ``_sdk`` argument, which is
the whole point of that seam: every status the boundary promises - ``ok``,
``refusal``, ``truncated``, ``invalid`` - is produced offline, including the two
that current ``openai-python`` raises instead of returning.

The one place the real SDK is used is construction, which makes no request: it
checks that ``OPENAI_API_KEY`` and ``OPENAI_BASE_URL`` reach the SDK without
this module re-parsing them.  Those tests skip when ``openai`` is not installed;
the constructor's own precondition failure is asserted either way.
"""

from __future__ import annotations

import os
import unittest
from types import SimpleNamespace
from unittest import mock

from argus.contracts import ContractError
from argus.fakes import FakeModelClient
from argus.model_client import (
    API_KEY_ENV_VAR,
    BASE_URL_ENV_VAR,
    MODEL_ENV_VAR,
    MODEL_STATUSES,
    ModelClient,
    ModelResult,
    OpenAICompatibleClient,
)

try:  # the SDK is optional; almost every test below runs without it
    import openai
except ImportError:  # pragma: no cover - depends on the environment
    openai = None

from pydantic import BaseModel


class Answer(BaseModel):
    """The tiny structured output the fake backend is asked for."""

    label: str
    score: int


# --------------------------------------------------------------------------- #
# A fake SDK, shaped like ``openai.OpenAI``
# --------------------------------------------------------------------------- #


class FakeCompletions:
    def __init__(self, outcome):
        self.outcome = outcome
        self.calls: list[dict] = []

    def parse(self, **kwargs):
        self.calls.append(kwargs)
        if isinstance(self.outcome, BaseException):
            raise self.outcome
        return self.outcome


class FakeSDK:
    """``sdk.chat.completions.parse`` returns - or raises - one canned outcome."""

    def __init__(self, outcome):
        self.completions = FakeCompletions(outcome)
        self.chat = SimpleNamespace(completions=self.completions)

    @property
    def calls(self):
        return self.completions.calls


def message(*, parsed=None, content=None, refusal=None):
    return SimpleNamespace(parsed=parsed, content=content, refusal=refusal)


class ExplodingMessage:
    """A refusal whose content blows up if anything reads it before the check."""

    refusal = "I will not help with that."

    @property
    def content(self):  # pragma: no cover - the test asserts it is not reached
        raise AssertionError("content was read before the refusal was checked")

    @property
    def parsed(self):  # pragma: no cover - the test asserts it is not reached
        raise AssertionError("content was read before the refusal was checked")


def completion(msg, *, finish_reason="stop", model="served-model-1"):
    return SimpleNamespace(
        model=model,
        choices=[SimpleNamespace(finish_reason=finish_reason, message=msg)],
    )


def client(outcome, *, model="test-model-1"):
    sdk = FakeSDK(outcome)
    return OpenAICompatibleClient(model=model, _sdk=sdk), sdk


def call(instance, *, system="be exact", user="the request", max_tokens=256):
    return instance.parse_json(
        system=system, user=user, output_model=Answer, max_tokens=max_tokens
    )


# --------------------------------------------------------------------------- #
# ModelResult
# --------------------------------------------------------------------------- #


class ModelResultTests(unittest.TestCase):
    def test_the_four_statuses(self):
        self.assertEqual(MODEL_STATUSES, ("ok", "refusal", "truncated", "invalid"))

    def test_defaults(self):
        result = ModelResult(status="ok")
        self.assertIsNone(result.parsed)
        self.assertIsNone(result.raw_text)
        self.assertEqual(result.model, "")

    def test_unknown_status_is_rejected(self):
        with self.assertRaises(ContractError) as caught:
            ModelResult(status="finished")
        self.assertIn("finished", str(caught.exception))

    def test_a_non_ok_result_may_not_carry_parsed_content(self):
        for status in ("refusal", "truncated", "invalid"):
            with self.assertRaises(ContractError):
                ModelResult(status=status, parsed={"label": "x"})

    def test_parsed_must_be_a_mapping(self):
        with self.assertRaises(ContractError):
            ModelResult(status="ok", parsed=[1, 2])


# --------------------------------------------------------------------------- #
# OpenAICompatibleClient - the four statuses
# --------------------------------------------------------------------------- #


class ParseJsonTests(unittest.TestCase):
    def test_ok_returns_the_validated_dump(self):
        instance, sdk = client(
            completion(message(parsed=Answer(label="a", score=1), content='{"label":"a"}'))
        )
        result = call(instance)
        self.assertEqual(result.status, "ok")
        self.assertEqual(result.parsed, {"label": "a", "score": 1})
        self.assertEqual(result.raw_text, '{"label":"a"}')
        # The model that answered, as the backend reported it.
        self.assertEqual(result.model, "served-model-1")

    def test_the_request_sent_to_the_sdk(self):
        instance, sdk = client(completion(message(parsed=Answer(label="a", score=1))))
        call(instance, system="rules", user="do it", max_tokens=99)
        self.assertEqual(len(sdk.calls), 1)
        sent = sdk.calls[0]
        self.assertEqual(sent["model"], "test-model-1")
        self.assertEqual(sent["max_tokens"], 99)
        # Structured output by schema, not by asking nicely in prose.
        self.assertIs(sent["response_format"], Answer)
        self.assertEqual(
            sent["messages"],
            [
                {"role": "system", "content": "rules"},
                {"role": "user", "content": "do it"},
            ],
        )

    def test_a_parsed_mapping_is_validated_rather_than_trusted(self):
        """An OpenAI-compatible server may hand back plain data, not a model."""
        instance, _ = client(completion(message(parsed={"label": "b", "score": 2})))
        result = call(instance)
        self.assertEqual(result.status, "ok")
        self.assertEqual(result.parsed, {"label": "b", "score": 2})

    def test_a_parsed_mapping_that_fails_validation_is_invalid(self):
        instance, _ = client(completion(message(parsed={"label": "b"})))
        result = call(instance)
        self.assertEqual(result.status, "invalid")
        self.assertIsNone(result.parsed)

    # -- refusal ----------------------------------------------------------- #

    def test_refusal_is_read_before_any_content(self):
        instance, _ = client(completion(ExplodingMessage()))
        result = call(instance)
        self.assertEqual(result.status, "refusal")
        self.assertEqual(result.raw_text, "I will not help with that.")
        self.assertIsNone(result.parsed)

    def test_a_refusal_beats_a_parsed_payload(self):
        """Content alongside a refusal is still never trusted."""
        instance, _ = client(
            completion(
                message(parsed=Answer(label="a", score=1), refusal="no", content="{}")
            )
        )
        result = call(instance)
        self.assertEqual(result.status, "refusal")
        self.assertIsNone(result.parsed)

    # -- truncated --------------------------------------------------------- #

    def test_finish_reason_length_is_truncated(self):
        instance, _ = client(
            completion(message(content='{"label": "a"'), finish_reason="length")
        )
        result = call(instance)
        self.assertEqual(result.status, "truncated")
        self.assertIsNone(result.parsed)
        self.assertEqual(result.raw_text, '{"label": "a"')

    @unittest.skipIf(openai is None, "the openai package is not installed")
    def test_the_sdks_length_error_is_truncated_not_an_exception(self):
        """Current openai-python raises rather than returning finish_reason."""
        raised = openai.LengthFinishReasonError(completion=SimpleNamespace(usage=None))
        instance, _ = client(raised)
        result = call(instance)
        self.assertEqual(result.status, "truncated")
        self.assertEqual(result.model, "test-model-1")

    @unittest.skipIf(openai is None, "the openai package is not installed")
    def test_the_sdks_content_filter_error_is_a_refusal(self):
        instance, _ = client(openai.ContentFilterFinishReasonError())
        result = call(instance)
        self.assertEqual(result.status, "refusal")
        self.assertIn("content filter", result.raw_text)

    # -- invalid ----------------------------------------------------------- #

    def test_no_parsed_content_is_invalid(self):
        instance, _ = client(completion(message(content="sorry, plain prose")))
        result = call(instance)
        self.assertEqual(result.status, "invalid")
        self.assertEqual(result.raw_text, "sorry, plain prose")

    def test_no_choices_is_invalid(self):
        instance, _ = client(SimpleNamespace(model="m", choices=[]))
        self.assertEqual(call(instance).status, "invalid")

    def test_a_validation_error_from_the_sdk_is_invalid(self):
        instance, _ = client(ValueError("2 validation errors for Answer"))
        result = call(instance)
        self.assertEqual(result.status, "invalid")
        self.assertIn("validation errors", result.raw_text)

    def test_a_transport_failure_is_not_swallowed_as_a_status(self):
        """Only what the *model* did becomes a status; a broken call still raises."""

        class Boom(Exception):
            pass

        instance, _ = client(Boom("connection reset"))
        with self.assertRaises(Boom):
            call(instance)

    def test_output_model_must_be_a_pydantic_model(self):
        instance, sdk = client(completion(message(parsed=Answer(label="a", score=1))))
        with self.assertRaises(ContractError) as caught:
            instance.parse_json(system="s", user="u", output_model=dict, max_tokens=8)
        self.assertEqual(caught.exception.code, "INVALID_INPUT")
        self.assertEqual(sdk.calls, [])


# --------------------------------------------------------------------------- #
# OpenAICompatibleClient - construction
# --------------------------------------------------------------------------- #


class ConstructionTests(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.dict(os.environ, {}, clear=False)
        patcher.start()
        self.addCleanup(patcher.stop)
        for name in (MODEL_ENV_VAR, API_KEY_ENV_VAR, BASE_URL_ENV_VAR):
            os.environ.pop(name, None)

    def test_the_model_argument_wins(self):
        os.environ[MODEL_ENV_VAR] = "from-env"
        instance = OpenAICompatibleClient(model="explicit", _sdk=FakeSDK(None))
        self.assertEqual(instance.model, "explicit")

    def test_the_model_falls_back_to_the_environment(self):
        os.environ[MODEL_ENV_VAR] = "from-env"
        self.assertEqual(
            OpenAICompatibleClient(_sdk=FakeSDK(None)).model, "from-env"
        )

    def test_no_model_anywhere_is_a_precondition_failure(self):
        """There is no default model; a run never guesses which one to call."""
        for absent in (None, "", "   "):
            with self.assertRaises(ContractError) as caught:
                OpenAICompatibleClient(model=absent, _sdk=FakeSDK(None))
            self.assertEqual(caught.exception.code, "PRECONDITION_FAILED")
            self.assertIn(MODEL_ENV_VAR, str(caught.exception))
            self.assertIn("no default model", str(caught.exception))

    def test_a_missing_sdk_is_a_precondition_failure(self):
        """The import lives in the constructor, so this is the only place it fails."""
        real_import = __import__

        def no_openai(name, *args, **kwargs):
            if name == "openai":
                raise ImportError("No module named 'openai'")
            return real_import(name, *args, **kwargs)

        with mock.patch("builtins.__import__", no_openai):
            with self.assertRaises(ContractError) as caught:
                OpenAICompatibleClient(model="m")
        self.assertEqual(caught.exception.code, "PRECONDITION_FAILED")
        self.assertIn("pip install openai", str(caught.exception))

    @unittest.skipIf(openai is None, "the openai package is not installed")
    def test_the_sdk_reads_the_credentials_this_module_does_not_parse(self):
        """No request is made here; only that the SDK, not us, reads the env."""
        os.environ[API_KEY_ENV_VAR] = "key-from-env"
        os.environ[BASE_URL_ENV_VAR] = "http://localhost:8000/v1"
        instance = OpenAICompatibleClient(model="m")
        self.assertEqual(instance._sdk.api_key, "key-from-env")
        self.assertEqual(str(instance._sdk.base_url).rstrip("/"), "http://localhost:8000/v1")

    @unittest.skipIf(openai is None, "the openai package is not installed")
    def test_explicit_credentials_override_the_environment(self):
        os.environ[API_KEY_ENV_VAR] = "key-from-env"
        instance = OpenAICompatibleClient(
            model="m", api_key="explicit-key", base_url="http://127.0.0.1:9/v1"
        )
        self.assertEqual(instance._sdk.api_key, "explicit-key")
        self.assertEqual(str(instance._sdk.base_url).rstrip("/"), "http://127.0.0.1:9/v1")

    @unittest.skipIf(openai is None, "the openai package is not installed")
    def test_a_missing_api_key_is_a_precondition_failure(self):
        with self.assertRaises(ContractError) as caught:
            OpenAICompatibleClient(model="m")
        self.assertEqual(caught.exception.code, "PRECONDITION_FAILED")
        self.assertIn("openai client", str(caught.exception))


# --------------------------------------------------------------------------- #
# The protocol, and the fake that implements it
# --------------------------------------------------------------------------- #


class ProtocolTests(unittest.TestCase):
    def test_both_implementations_satisfy_the_protocol(self):
        self.assertIsInstance(
            OpenAICompatibleClient(model="m", _sdk=FakeSDK(None)), ModelClient
        )
        self.assertIsInstance(FakeModelClient(ModelResult(status="ok")), ModelClient)


class FakeModelClientTests(unittest.TestCase):
    def test_one_result_may_be_given_without_a_list(self):
        fake = FakeModelClient(ModelResult(status="ok", parsed={"a": 1}, model="m"))
        self.assertEqual(call(fake).parsed, {"a": 1})

    def test_results_come_back_in_order(self):
        first = ModelResult(status="ok", parsed={"n": 1}, model="m")
        second = ModelResult(status="refusal", raw_text="no", model="m")
        fake = FakeModelClient([first, second])
        self.assertIs(call(fake), first)
        self.assertIs(call(fake), second)

    def test_every_call_is_recorded(self):
        fake = FakeModelClient(ModelResult(status="ok", model="m"))
        call(fake, system="rules", user="do it", max_tokens=17)
        self.assertEqual(
            fake.calls,
            [
                {
                    "system": "rules",
                    "user": "do it",
                    "output_model": Answer,
                    "max_tokens": 17,
                }
            ],
        )

    def test_running_out_of_results_is_an_error_not_a_repeat(self):
        fake = FakeModelClient(ModelResult(status="ok", model="m"))
        call(fake)
        with self.assertRaises(ContractError) as caught:
            call(fake)
        self.assertIn("called 2 time(s)", str(caught.exception))

    def test_only_model_results_may_be_scripted(self):
        with self.assertRaises(ContractError):
            FakeModelClient([{"status": "ok"}])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
