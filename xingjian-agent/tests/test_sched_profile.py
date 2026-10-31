"""Offline tests for the scheduling profile.

Run with ``python -m unittest discover -s tests`` from the project root.
No network and no API key: the module is arithmetic over plain dicts.

The tests are written against the *rules*, not the numbers: each one
names the paragraph of ``用户画像类型与排布策略.md`` it protects, so a
future tuning pass can move constants without silently dropping a rule.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import sched_profile as sp  # noqa: E402

DAY = 86400.0
BASE = 1_700_000_000.0  # fixed clock, so decay is deterministic


def seeded(values, *, age_days=30.0, key="estimate_bias"):
    """A profile whose history is *values*, all ``age_days`` old."""
    profile = sp.empty_profile()
    start = BASE - age_days * DAY
    for index, value in enumerate(values):
        profile, _ = sp.observe(profile, key, value, ts=start + index)
    return profile


class NoiseVersusDriftTest(unittest.TestCase):
    """P-5: one excursion is noise, three in a row is a step change."""

    def test_single_excursion_does_not_move_posterior(self):
        profile = seeded([1.5] * 5)
        before = profile["indicators"]["estimate_bias"]["median"]
        samples = len(profile["indicators"]["estimate_bias"]["samples"])

        profile, outcome = sp.observe(profile, "estimate_bias", 9.0, ts=BASE)

        self.assertEqual(outcome["status"], "outlier")
        record = profile["indicators"]["estimate_bias"]
        self.assertEqual(record["median"], before)
        self.assertEqual(len(record["samples"]), samples)
        self.assertEqual(profile["outliers"]["estimate_bias"], 1)

    def test_two_excursions_still_noise(self):
        profile = seeded([1.5] * 5)
        for offset in range(2):
            profile, outcome = sp.observe(
                profile, "estimate_bias", 9.0, ts=BASE + offset
            )
        self.assertEqual(outcome["status"], "outlier")
        self.assertEqual(profile["indicators"]["estimate_bias"]["median"], 1.5)

    def test_three_excursions_are_a_step_change(self):
        profile = seeded([1.5] * 5)
        for offset in range(3):
            profile, outcome = sp.observe(
                profile, "estimate_bias", 3.5, ts=BASE + offset
            )

        self.assertEqual(outcome["status"], "accepted")
        self.assertEqual(outcome["reason"], "drift")
        record = profile["indicators"]["estimate_bias"]
        self.assertEqual(record["state"], "drift")
        self.assertGreater(record["median"], 3.0)

    def test_ordinary_observation_is_accepted(self):
        profile = seeded([1.5] * 5)
        profile, outcome = sp.observe(profile, "estimate_bias", 1.6, ts=BASE)
        self.assertEqual(outcome["status"], "accepted")
        self.assertEqual(outcome["reason"], "update")


class SourceGateTest(unittest.TestCase):
    """7.12: dialogue may move preferences, never capability estimates."""

    def test_reaction_cannot_move_capability(self):
        profile = sp.empty_profile()
        untouched, outcome = sp.observe(
            profile, "estimate_bias", 3.0, source="reaction"
        )
        self.assertEqual(outcome["status"], "rejected")
        self.assertEqual(outcome["reason"], "source_not_allowed")
        self.assertNotIn("estimate_bias", untouched["indicators"])

    def test_model_output_is_never_a_source(self):
        profile = sp.empty_profile()
        _, outcome = sp.observe(
            profile, "estimate_bias", 3.0, source="model"
        )
        self.assertEqual(outcome["status"], "rejected")
        self.assertFalse(sp.is_allowed_source("capability", "model"))

    def test_self_report_is_a_weak_prior(self):
        profile = sp.empty_profile()
        profile, outcome = sp.observe(
            profile, "estimate_bias", 4.0, source="self_report", kind="preference"
        )
        self.assertEqual(outcome["status"], "accepted")
        record = profile["indicators"]["estimate_bias"]
        # Pulled toward the prior instead of taken at face value.
        self.assertLess(record["median"], 4.0)
        self.assertGreater(record["median"], 1.2)


class DeltaProtocolTest(unittest.TestCase):
    """P-1..P-4: only quoted, whitelisted, capped writes survive."""

    TEXT = "我一般只能专注二十五分钟，早上效率高，晚上别排，周末想留白"

    def setUp(self):
        self.profile = sp.empty_profile()

    def test_quote_must_appear_in_user_text(self):
        raw = {
            "set": [
                {
                    "key": "block",
                    "value": "25",
                    "quote": "我一般只能专注二十五分钟",
                }
            ]
        }
        clean, rejects = sp.validate_delta(raw, self.TEXT)
        self.assertEqual(len(clean["set"]), 1)
        self.assertEqual(rejects, [])

        clean, rejects = sp.validate_delta(
            {"set": [{"key": "block", "value": "25", "quote": "我晚上效率最高"}]},
            self.TEXT,
        )
        self.assertEqual(clean["set"], [])
        self.assertEqual(rejects[0]["reason"], "quote_mismatch")

    def test_meta_instruction_is_refused(self):
        raw = {
            "set": [
                {
                    "key": "rhythm",
                    "value": "morning",
                    "quote": "记住我喜欢早上",
                }
            ]
        }
        clean, rejects = sp.validate_delta(raw, "记住我喜欢早上效率高")
        self.assertEqual(clean["set"], [])
        self.assertEqual(rejects[0]["reason"], "meta_instruction")

    def test_unknown_slot_is_refused(self):
        clean, rejects = sp.validate_delta(
            {"set": [{"key": "estimate_bias", "value": "2.0", "quote": "只能专注二十五分钟"}]},
            self.TEXT,
        )
        self.assertEqual(clean["set"], [])
        self.assertEqual(rejects[0]["reason"], "unknown_slot")

    def test_at_most_three_writes_per_turn(self):
        raw = {
            "set": [
                {"key": "block", "value": "25", "quote": "只能专注二十五分钟"},
                {"key": "rhythm", "value": "morning", "quote": "早上效率高"},
                {"key": "avoid", "value": ["22:00"], "quote": "晚上别排"},
                {"key": "density", "value": "sparse", "quote": "想留白"},
            ]
        }
        clean, rejects = sp.validate_delta(raw, self.TEXT)
        self.assertEqual(len(clean["set"]), sp.MAX_DELTA)
        self.assertEqual(rejects[-1]["reason"], "over_limit")

    def test_version_mismatch_drops_everything(self):
        raw = {"base_version": 99, "set": [{"key": "block", "value": "25", "quote": "只能专注二十五分钟"}]}
        clean, rejects = sp.validate_delta(raw, self.TEXT, base_version=3)
        self.assertEqual(clean["set"], [])
        self.assertEqual(rejects[0]["reason"], "version_mismatch")

    def test_write_is_visible_only_from_the_next_turn(self):
        clean, _ = sp.validate_delta(
            {"set": [{"key": "block", "value": "45", "quote": "只能专注二十五分钟"}]},
            self.TEXT,
        )
        profile = sp.apply_delta(self.profile, clean, turn=3)

        self.assertNotIn("单块=45分钟", sp.prompt_block(profile, turn=3))
        self.assertIn("单块=45分钟", sp.prompt_block(profile, turn=4))

    def test_retire_keeps_the_evidence(self):
        clean, _ = sp.validate_delta(
            {"set": [{"key": "block", "value": "25", "quote": "只能专注二十五分钟"}]},
            self.TEXT,
        )
        profile = sp.apply_delta(self.profile, clean, turn=1)
        profile = sp.apply_delta(profile, {"retire": ["block"]}, turn=2)

        self.assertNotIn("block", profile["slots"])
        self.assertIn("block", profile["retired"])
        self.assertEqual(
            profile["retired"]["block"]["quote"], "只能专注二十五分钟"
        )


class ExpectationTest(unittest.TestCase):
    """7.8: posture needs three of the same cue, one level at a time."""

    def test_three_cues_raise_the_posture(self):
        profile = sp.empty_profile()
        for _ in range(3):
            profile, outcome = sp.observe_expectation(profile, "ask_harder")
        self.assertTrue(outcome["moved"])
        self.assertEqual(sp.expectation_level(profile), "challenge")

    def test_alternating_cues_do_not_move(self):
        profile = sp.empty_profile()
        for signal in ("accept_conservative", "ask_harder", "accept_conservative"):
            profile, _ = sp.observe_expectation(profile, signal)
        self.assertEqual(sp.expectation_level(profile), "neutral")

    def test_posture_moves_one_level_per_round(self):
        profile = sp.empty_profile()
        for _ in range(6):
            profile, _ = sp.observe_expectation(profile, "accept_conservative")
        self.assertEqual(sp.expectation_level(profile), "guard")


class BetTest(unittest.TestCase):
    """7.8: the prediction is honest, the bet follows the posture."""

    def _profile_at(self, level):
        profile = sp.empty_profile()
        profile["expectation"]["level"] = level
        return profile

    def test_bet_brackets_the_prediction(self):
        guard = sp.bet(self._profile_at("guard"), 0.6)
        neutral = sp.bet(self._profile_at("neutral"), 0.6)
        challenge = sp.bet(self._profile_at("challenge"), 0.6)

        self.assertLess(guard["bet"], neutral["bet"])
        self.assertGreater(challenge["bet"], neutral["bet"])
        self.assertAlmostEqual(neutral["bet"], 0.6, places=3)
        self.assertEqual(challenge["predicted"], 0.6)  # the forecast never moves

    def test_bet_never_leaves_zero_one(self):
        self.assertLessEqual(sp.bet(self._profile_at("challenge"), 0.95)["bet"], 0.95)
        self.assertGreaterEqual(sp.bet(self._profile_at("guard"), 0.02)["bet"], 0.05)


class ShrinkageTest(unittest.TestCase):
    """7.9: with few samples the prior dominates, then it lets go."""

    def test_empty_profile_reads_the_prior(self):
        self.assertAlmostEqual(sp.shrunk(sp.empty_profile(), "estimate_bias"), 1.2)

    def test_prior_releases_as_evidence_grows(self):
        profile = sp.empty_profile()
        for index in range(5):
            profile, _ = sp.observe(
                profile, "estimate_bias", 2.0, ts=BASE - 5 * DAY + index
            )
        value = sp.shrunk(profile, "estimate_bias")
        self.assertGreater(value, 1.2)
        self.assertLess(value, 2.0)


class StyleTest(unittest.TestCase):
    """7.11: learn from the user, mirror at most one step."""

    def test_model_text_is_not_a_style_source(self):
        with self.assertRaises(sp.ProfileError):
            sp.observe_style(sp.empty_profile(), "你好呀", source="model")

    def test_short_casual_user_is_mirrored_once(self):
        profile = sp.empty_profile()
        for _ in range(4):
            profile = sp.observe_style(profile, "嗯嗯好的呀")
        summary = sp.style_summary(profile)
        self.assertEqual(summary.get("length"), "short")
        self.assertEqual(summary.get("formality"), "casual")

    def test_emoji_is_never_mirrored_at_full_strength(self):
        profile = sp.empty_profile()
        for _ in range(4):
            profile = sp.observe_style(profile, "好耶😀😀")
        self.assertNotEqual(sp.style_summary(profile).get("emoji"), "frequent")


class RetentionTest(unittest.TestCase):
    """7.10 / P-10: purge needs a reason, merge needs a version."""

    def test_purge_needs_two_rejections(self):
        profile = sp.empty_profile()
        self.assertEqual(sp.purge_candidates(profile, rejections={"rhythm": 1}), [])
        reasons = sp.purge_candidates(profile, rejections={"rhythm": 2})
        self.assertEqual(reasons[0]["reason"], "rejected_twice")

    def test_decayed_slot_is_a_purge_candidate(self):
        profile = sp.empty_profile()
        profile["slots"]["block"] = {
            "value": "25",
            "quote": "只能专注二十五分钟",
            "turn": 1,
            "ts": BASE - 200 * DAY,
        }
        reasons = sp.purge_candidates(profile, now=BASE)
        self.assertIn("decayed", [item["reason"] for item in reasons])

    def test_merge_takes_the_newer_slot(self):
        left = sp.empty_profile()
        left["version"] = 1
        left["slots"]["block"] = {"value": "25", "turn": 1, "ts": BASE}
        right = sp.empty_profile()
        right["version"] = 2
        right["slots"]["block"] = {"value": "45", "turn": 4, "ts": BASE + 1}

        merged = sp.merge(left, right)
        self.assertEqual(merged["slots"]["block"]["value"], "45")
        self.assertGreater(merged["version"], 2)


class PromptBlockTest(unittest.TestCase):
    """7.4: the model receives parameters, not labels."""

    def test_block_is_bounded_and_label_free(self):
        profile = seeded([1.6] * 9)
        profile["slots"].update(
            {
                "rhythm": {"value": "morning", "turn": 0, "ts": BASE},
                "block": {"value": "25", "turn": 0, "ts": BASE},
                "buffer": {"value": "10", "turn": 0, "ts": BASE},
                "avoid": {"value": ["22:00"], "turn": 0, "ts": BASE},
                "density": {"value": "sparse", "turn": 0, "ts": BASE},
                "note": {"value": "下周实习", "expire": "2026-11-07", "turn": 0, "ts": BASE},
            }
        )
        block = sp.prompt_block(profile, turn=9)

        self.assertLessEqual(len(block), sp.MAX_PROMPT_CHARS)
        self.assertIn("单块=25分钟", block)
        self.assertIn("禁排=22:00后", block)
        for label in ("乐观", "保守", "型", "画像"):
            self.assertNotIn(label, block)

    def test_scheduling_params_stay_inside_the_whitelist(self):
        profile = seeded([1.8] * 9)
        params = sp.scheduling_params(profile)
        self.assertEqual(params["proactive"], "L1")  # never granted by the profile
        self.assertLess(params["daily_ratio"], 0.8)
        self.assertGreaterEqual(params["block_minutes"], 15)

    def test_block_rate_needs_evidence(self):
        profile = sp.empty_profile()
        self.assertIsNone(sp.block_rate(profile, "25"))
        profile, _ = sp.observe(
            profile, "block_success", True, bucket="25", ts=BASE
        )
        self.assertAlmostEqual(sp.block_rate(profile, "25"), 1.0)


class WiringTest(unittest.TestCase):
    """The rule must reach the prompt without ever reaching the browser."""

    def test_profile_block_is_its_own_system_message(self):
        import prompts

        messages = prompts.chat_messages(
            [{"role": "user", "content": "帮我调一下"}],
            {"goals": [], "tasks": []},
            "单块=25分钟",
        )
        self.assertEqual(
            [item["role"] for item in messages],
            ["system", "system", "system", "user"],
        )
        self.assertIn("<persona>\n单块=25分钟\n</persona>",
                      [item["content"] for item in messages])

    def test_empty_profile_adds_nothing(self):
        import prompts

        messages = prompts.chat_messages(
            [{"role": "user", "content": "帮我调一下"}],
            {"goals": []},
        )
        self.assertEqual(len(messages), 3)

    def test_persona_travels_beside_the_proposal(self):
        import deepseek

        raw = (
            '{"summary":"移动","changes":[{"type":"remove_event",'
            '"date":"2026-10-31","title":"临时会议"}],'
            '"persona":{"set":[{"key":"block","value":"25","quote":"只能专注"}]}}'
        )
        proposal, persona = deepseek.parse_turn(raw, 1)
        self.assertEqual(len(proposal["changes"]), 1)
        self.assertEqual(persona["set"][0]["key"], "block")
        # The proposal handed to the browser carries no persona payload.
        self.assertNotIn("persona", proposal)

    def test_persona_alone_is_not_a_proposal(self):
        import deepseek

        raw = '{"summary":"x","changes":[],"persona":{"set":[]}}'
        proposal, persona = deepseek.parse_turn(raw, None)
        self.assertIsNone(proposal)
        self.assertIsNotNone(persona)


if __name__ == "__main__":
    unittest.main()
