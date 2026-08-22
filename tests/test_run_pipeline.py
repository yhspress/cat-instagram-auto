from __future__ import annotations

import os
import json
import sys
import types
import unittest
from datetime import datetime
from importlib.util import find_spec
from pathlib import Path
from unittest.mock import Mock, patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

# Keep the pure validation/publishing tests runnable in minimal local Python
# environments. GitHub Actions installs the real runtime dependencies first.
if find_spec("requests") is None:
    requests_stub = types.ModuleType("requests")
    requests_stub.post = Mock()
    requests_stub.get = Mock()
    requests_stub.RequestException = Exception
    sys.modules["requests"] = requests_stub
if find_spec("PIL") is None:
    pil_stub = types.ModuleType("PIL")
    pil_stub.Image = Mock()
    sys.modules["PIL"] = pil_stub
if find_spec("openai") is None:
    openai_stub = types.ModuleType("openai")
    openai_stub.OpenAI = Mock
    sys.modules["openai"] = openai_stub

import run_pipeline as rp


class StoryBatchValidationTests(unittest.TestCase):
    def make_batch(self) -> tuple[dict, list[list[dict[str, str]]]]:
        expressions = [
            "wide-eyed astonishment with ears sharply forward",
            "dramatic comic hiss with ears naturally angled back",
            "intense suspicious side-eye with tense whisker pads",
            "triumphant relief with relaxed whiskers and upright tail",
            "deeply offended stare with narrowed eyes and a tail flick",
            "startled open-mouthed gasp with round alert eyes",
            "mischievous nose-lick with bright focused eyes",
            "sleepy contentment with heavy relaxed eyelids",
            "smug satisfaction with a subtle one-sided squint",
            "focused determination with eyes locked forward",
        ]
        body_languages = [
            "one white paw frozen mid-reach with the tail held rigid",
            "balanced retreat with arched back and both front paws grounded",
            "low cautious crouch with ears sideways and tail tucked close",
            "upright proud stance with relaxed whiskers and tail raised",
            "forward lean with one paw planted and the tail snapping sideways",
            "sudden backward hop with both front paws lifted safely",
            "playful low bow with hindquarters raised and tail curled",
            "loose sleepy drape with chin and paws over a low ledge",
            "balanced seated pose with chest high and tail wrapped aside",
            "purposeful stride with one paw extended and ears aimed forward",
        ]
        stories = []
        assignments = []
        role_names = [role for role, _role_ko, _source_type, _selector in rp.SOURCE_ROLE_SPECS]
        for index, expression in enumerate(expressions):
            sources = []
            assigned_sources = []
            for offset, role in enumerate(role_names):
                source_id = f"TEST-{index * len(role_names) + offset + 1:03d}"
                category = f"category-{index}-{offset}"
                sources.append({"id": source_id, "role": role, "name_ko": "소스", "category_ko": category})
                assigned_sources.append({
                    "id": source_id,
                    "role": role,
                    "name_en": expression if role == "expression" else body_languages[index] if role == "gesture" else "assigned source",
                })
            prompt = (
                "Ultra-photorealistic live-action photography, vertical 4:5. "
                "An attractive adult Korean Shorthair cheese-tabby cat with all four paws clean white, "
                "a distinctive curry stain marking around the mouth, and round amber-brown eyes reacts "
                "at the peak of an extraordinary event with "
                f"{expression}; {body_languages[index]}."
            )
            stories.append({
                "title_ko": f"제목 {index}",
                "hook": f"A strange event {index}",
                "caption_explanation_en": "One impossible moment stops the cat in its tracks.",
                "hashtags": ["#Cat", "#Story", "#Surprise"],
                "hero_expression_en": expression,
                "hero_body_language_en": body_languages[index],
                "source_concepts": sources,
                "creative_fingerprint": {
                    "location_ko": f"장소 {index}",
                    "core_prop_ko": f"소품 {index}",
                    "twist_ko": f"반전 {index}",
                },
                "images": [{
                    "role": "HERO",
                    "camera_strategy": f"camera setup {index}",
                    "image_prompt": prompt,
                }],
            })
            assignments.append(assigned_sources)
        return {"stories": stories}, assignments

    def test_accepts_configured_ten_single_image_stories(self) -> None:
        batch, assignments = self.make_batch()
        self.assertEqual(rp.validate_story_batch(batch, assignments, {"batch_size": 10}), [])

    def test_rejects_legacy_three_image_story(self) -> None:
        batch, assignments = self.make_batch()
        batch["stories"][0]["images"] *= 3
        errors = rp.validate_story_batch(batch, assignments, {"batch_size": 10})
        self.assertIn("story 1: images must contain exactly 1 item", errors)

    def test_requires_expression_phrase_inside_prompt(self) -> None:
        batch, assignments = self.make_batch()
        batch["stories"][0]["images"][0]["image_prompt"] = (
            "Ultra-photorealistic live-action photography, vertical 4:5, Korean Shorthair."
        )
        errors = rp.validate_story_batch(batch, assignments, {"batch_size": 10})
        self.assertIn("story 1 image 1: prompt must include hero_expression_en verbatim", errors)

    def test_requires_body_language_phrase_inside_prompt(self) -> None:
        batch, assignments = self.make_batch()
        body_language = batch["stories"][0]["hero_body_language_en"]
        prompt = batch["stories"][0]["images"][0]["image_prompt"]
        batch["stories"][0]["images"][0]["image_prompt"] = prompt.replace(body_language, "neutral pose")
        errors = rp.validate_story_batch(batch, assignments, {"batch_size": 10})
        self.assertIn("story 1 image 1: prompt must include hero_body_language_en verbatim", errors)

    def test_requires_full_fixed_protagonist_appearance_inside_prompt(self) -> None:
        batch, assignments = self.make_batch()
        batch["stories"][0]["images"][0]["image_prompt"] = batch["stories"][0]["images"][0]["image_prompt"].replace(
            "round amber-brown eyes", "bright eyes"
        )
        errors = rp.validate_story_batch(batch, assignments, {"batch_size": 10})
        self.assertIn("story 1 image 1: missing 'round amber-brown eyes'", errors)

    def test_rejects_any_source_id_not_exactly_assigned(self) -> None:
        batch, assignments = self.make_batch()
        batch["stories"][0]["source_concepts"][2]["id"] = "999"
        errors = rp.validate_story_batch(batch, assignments, {"batch_size": 10})
        self.assertTrue(any("source_concepts ids must exactly match assigned ids" in error for error in errors))

    def test_accepts_assigned_ids_in_a_different_order(self) -> None:
        batch, assignments = self.make_batch()
        batch["stories"][0]["source_concepts"].reverse()
        self.assertEqual(rp.validate_story_batch(batch, assignments, {"batch_size": 10}), [])

    def test_rejects_duplicate_assigned_id(self) -> None:
        batch, assignments = self.make_batch()
        batch["stories"][0]["source_concepts"][1]["id"] = batch["stories"][0]["source_concepts"][0]["id"]
        errors = rp.validate_story_batch(batch, assignments, {"batch_size": 10})
        self.assertTrue(any("source_concepts ids must exactly match assigned ids" in error for error in errors))

    def test_requires_assigned_expression_and_gesture_names(self) -> None:
        batch, assignments = self.make_batch()
        batch["stories"][0]["hero_expression_en"] = "another expression"
        batch["stories"][0]["hero_body_language_en"] = "another gesture"
        errors = rp.validate_story_batch(batch, assignments, {"batch_size": 10})
        self.assertIn("story 1: hero_expression_en must match assigned expression name_en", errors)
        self.assertIn("story 1: hero_body_language_en must match assigned gesture name_en", errors)

    def test_requires_seven_distinct_expressions_and_gestures(self) -> None:
        batch, assignments = self.make_batch()
        first_expression = batch["stories"][0]["hero_expression_en"]
        first_gesture = batch["stories"][0]["hero_body_language_en"]
        for story in batch["stories"][6:]:
            prompt = story["images"][0]["image_prompt"]
            prompt = prompt.replace(story["hero_expression_en"], first_expression)
            prompt = prompt.replace(story["hero_body_language_en"], first_gesture)
            story["hero_expression_en"] = first_expression
            story["hero_body_language_en"] = first_gesture
            story["images"][0]["image_prompt"] = prompt

        errors = rp.validate_story_batch(batch, assignments, {"batch_size": 10})
        self.assertIn("stories must use at least 7 distinct hero_expression_en values", errors)
        self.assertIn("stories must use at least 7 distinct hero_body_language_en values", errors)


class SourceAssignmentTests(unittest.TestCase):
    def test_assigns_high_concept_roles_per_story_without_batch_id_reuse(self) -> None:
        concepts = (ROOT / "data" / "cat_concepts_500.txt").read_text(encoding="utf-8-sig")
        with patch.object(rp, "recent_source_ids", return_value=(set(), 0)):
            first = rp.assign_source_concepts(concepts, {"batch_size": 10})
            second = rp.assign_source_concepts(concepts, {"batch_size": 10})

        expected_roles = [role for role, _role_ko, _source_type, _selector in rp.SOURCE_ROLE_SPECS]
        self.assertEqual(len(rp.parse_concepts(concepts)), 500)
        self.assertEqual(first, second)
        self.assertEqual(len(first), 10)
        self.assertTrue(all([item["role"] for item in story] == expected_roles for story in first))
        for story in first:
            for item, (_role, _role_ko, source_type, selector) in zip(story, rp.SOURCE_ROLE_SPECS):
                if source_type == "high_concept":
                    self.assertEqual(item["category_ko"], f"HIGH_CONCEPT/{selector}")
                else:
                    self.assertIn(item["category_ko"], selector)
        ids = [item["id"] for story in first for item in story]
        self.assertEqual(len(ids), 80)
        self.assertEqual(len(set(ids)), 80)

    def test_high_concept_pool_is_well_formed_and_assigns_all_eight_axes(self) -> None:
        pool = rp.load_high_concept_pool(rp.load_config())
        self.assertEqual(set(pool), {"worlds", "events", "roles", "expressions", "gestures", "visual_hooks"})
        self.assertTrue(all(len(entries) >= 10 for entries in pool.values()))
        concepts = (ROOT / "data" / "cat_concepts_500.txt").read_text(encoding="utf-8-sig")
        with patch.object(rp, "recent_source_ids", return_value=(set(), 0)):
            assignments = rp.assign_source_concepts(concepts, rp.load_config())
        self.assertTrue(all(len(story) == len(rp.SOURCE_ROLE_SPECS) for story in assignments))
        self.assertEqual([item["role"] for item in assignments[0]], [spec[0] for spec in rp.SOURCE_ROLE_SPECS])

    def test_assignment_avoids_recent_ids_when_each_pool_has_capacity(self) -> None:
        concepts = (ROOT / "data" / "cat_concepts_500.txt").read_text(encoding="utf-8-sig")
        recent_ids = set()
        for entries in rp.load_high_concept_pool(rp.load_config()).values():
            recent_ids.update(record["id"] for record in entries[:5])
        records = rp.parse_concepts(concepts)
        for _role, _role_ko, source_type, selector in rp.SOURCE_ROLE_SPECS:
            if source_type == "concept_library":
                pool = [record for record in records if record["category_ko"] in selector]
                recent_ids.update(record["id"] for record in pool[:10])

        with patch.object(rp, "recent_source_ids", return_value=(recent_ids, 0)):
            assignments = rp.assign_source_concepts(concepts, rp.load_config())

        assigned_ids = {item["id"] for story in assignments for item in story}
        self.assertTrue(assigned_ids.isdisjoint(recent_ids))

    def test_compact_assignment_prompt_contains_only_assigned_concepts(self) -> None:
        concepts = (ROOT / "data" / "cat_concepts_500.txt").read_text(encoding="utf-8-sig")
        with patch.object(rp, "recent_source_ids", return_value=(set(), 0)):
            assignments = rp.assign_source_concepts(concepts, rp.load_config())
        compact = rp.assigned_source_prompt(assignments)

        self.assertEqual(compact.count('"story_index"'), 10)
        self.assertEqual(compact.count('"id"'), 80)
        self.assertNotIn("500. ", compact)

    def test_generation_injects_assignments_instead_of_full_library(self) -> None:
        batch, assignments = StoryBatchValidationTests().make_batch()
        response = Mock(output_text=json.dumps(batch, ensure_ascii=False))
        client = Mock()
        client.responses.create.return_value = response
        config = {"story_prompt_file": "prompts/story_generator_prompt.txt", "batch_size": 10}

        with (
            patch.object(rp, "assign_source_concepts", return_value=assignments),
            patch.object(rp, "history_for_prompt", return_value="[]"),
            patch.object(rp, "required_env", return_value="text-model"),
            patch.object(rp, "get_now", return_value=datetime(2026, 1, 2, 3, 4, 5)),
        ):
            stories = rp.generate_story_batch(client, config, "FULL_LIBRARY_SENTINEL")

        sent_prompt = client.responses.create.call_args.kwargs["input"]
        self.assertEqual(len(stories), 10)
        self.assertIn("ASSIGNED_SOURCE_CONCEPTS", sent_prompt)
        self.assertIn("create exactly 10 original", sent_prompt)
        self.assertIn("at least 7 clearly different", sent_prompt)
        self.assertNotIn("{batch_size}", sent_prompt)
        self.assertNotIn("{minimum_hero_variety}", sent_prompt)
        self.assertNotIn("{assigned_source_concepts}", sent_prompt)
        self.assertNotIn("FULL_LIBRARY_SENTINEL", sent_prompt)

    def test_prompt_requires_random_supporting_cats_and_safe_original_homages(self) -> None:
        prompt = (ROOT / "prompts" / "story_generator_prompt.txt").read_text(encoding="utf-8")
        self.assertIn("RANDOM SUPPORTING CATS", prompt)
        self.assertIn("Never duplicate or visually confuse them with the fixed cheese-tabby protagonist", prompt)
        self.assertIn("Never reproduce a famous artwork, movie poster, character, costume", prompt)


class QueueBatchTests(unittest.TestCase):
    def test_empty_queue_refills_with_configured_ten_stories(self) -> None:
        generated = [{"story_id": f"story-{index}"} for index in range(10)]
        with (
            patch.object(rp, "queue_data", return_value={"stories": []}),
            patch.object(rp, "generate_story_batch", return_value=generated),
            patch.object(rp, "save_json") as save_json,
        ):
            queue = rp.ensure_queue(Mock(), {"batch_size": 10, "queue_refill_threshold": 1}, "concepts")

        self.assertEqual(len(queue["stories"]), 10)
        save_json.assert_called_once_with(rp.QUEUE_PATH, queue)

    def test_successful_publish_consumes_only_one_story(self) -> None:
        queue = {"stories": [{"story_id": f"story-{index}"} for index in range(10)]}
        prepared = {
            "publication_key": "2026-08-22:01",
            "story_id": "story-0",
            "story": {
                "story_id": "story-0",
                "title_ko": "제목",
                "hook": "Hook",
                "source_concepts": [],
                "creative_fingerprint": {},
            },
        }

        def load_state(path: Path, default: dict) -> dict:
            if path == rp.PUBLISHED_PATH:
                return {"published": [], "posts": []}
            if path == rp.QUEUE_PATH:
                return queue
            if path == rp.HISTORY_PATH:
                return {"stories": []}
            return default

        with (
            patch.object(rp, "load_json", side_effect=load_state),
            patch.object(rp, "save_json") as save_json,
        ):
            rp.finalize_success(prepared, "media-1", ["https://example.com/hero.jpg"])

        saved_queue = next(call.args[1] for call in save_json.call_args_list if call.args[0] == rp.QUEUE_PATH)
        self.assertEqual(len(saved_queue["stories"]), 9)
        self.assertEqual(saved_queue["stories"][0]["story_id"], "story-1")


class SingleImagePublishingTests(unittest.TestCase):
    @patch.dict(os.environ, {"INSTAGRAM_USER_ID": "user-1", "INSTAGRAM_ACCESS_TOKEN": "token"}, clear=False)
    @patch.object(rp.time, "sleep")
    @patch.object(rp.requests, "post")
    @patch.object(rp, "wait_container")
    @patch.object(rp, "ig_post")
    def test_publishes_one_plain_image_container(
        self,
        ig_post: Mock,
        wait_container: Mock,
        requests_post: Mock,
        _sleep: Mock,
    ) -> None:
        ig_post.return_value = {"id": "container-1"}
        response = Mock(ok=True)
        response.json.return_value = {"id": "media-1"}
        requests_post.return_value = response

        media_id = rp.publish_single_image({}, ["https://example.com/hero.jpg"], "caption")

        self.assertEqual(media_id, "media-1")
        payload = ig_post.call_args.args[2]
        self.assertEqual(payload["image_url"], "https://example.com/hero.jpg")
        self.assertEqual(payload["caption"], "caption")
        self.assertNotIn("media_type", payload)
        self.assertNotIn("is_carousel_item", payload)
        wait_container.assert_called_once_with({}, "container-1", "token")

    def test_rejects_any_media_count_other_than_one(self) -> None:
        with self.assertRaisesRegex(SystemExit, "exactly 1 media URL"):
            rp.publish_single_image({}, [], "caption")


if __name__ == "__main__":
    unittest.main()
