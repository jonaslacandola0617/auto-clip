import json
import unittest
from pathlib import Path

from autoclip.ai import AIResponseError, validate_candidate_payload
from autoclip.candidates import CandidateValidationError, normalize_and_deduplicate, validate_candidate
from dataclasses import replace

from tests.helpers import mt, sample_candidate, sample_source, sample_transcript


class AICandidateTests(unittest.TestCase):
    def test_valid_fixture(self) -> None:
        payload = json.loads((Path(__file__).parent / "fixtures" / "valid_ai_response.json").read_text(encoding="utf-8"))
        self.assertEqual(len(validate_candidate_payload(payload)), 2)

    def test_invalid_ai_response_rejected(self) -> None:
        payload = json.loads((Path(__file__).parent / "fixtures" / "invalid_ai_response.json").read_text(encoding="utf-8"))
        with self.assertRaises(AIResponseError):
            validate_candidate_payload(payload)

    def test_invalid_ranges_rejected_before_media_execution(self) -> None:
        base = sample_candidate()
        for candidate in (
            replace(base, source_start=mt(-1)),
            replace(base, source_end=mt(121)),
            replace(base, source_start=mt(28), source_end=mt(8)),
            replace(base, source_start=mt(8), source_end=mt(9)),
            replace(base, source_start=mt(80), source_end=mt(100)),
        ):
            with self.subTest(candidate=candidate):
                with self.assertRaises(CandidateValidationError):
                    validate_candidate(candidate, sample_source().duration, sample_transcript())

    def test_overlap_deduplication_keeps_higher_rank(self) -> None:
        high = sample_candidate()
        low = replace(high, id="candidate_002", source_start=mt(9), source_end=mt(27), scores=replace(high.scores, hook=10))
        self.assertEqual([high.id], [item.id for item in normalize_and_deduplicate([low, high])])


if __name__ == "__main__":
    unittest.main()

