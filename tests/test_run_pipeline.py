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
        ]
        body_languages = [
            "one white paw frozen mid-reach with the tail held rigid",
            "balanced retreat with arched back and both front paws grounded",
            "low cautious crouch with ears sideways and tail tucked close",
            "upright proud stance with relaxed whiskers and tail raised",
            "forward lean with one paw planted and the tail snapping sideways",
        ]
        stories = []
        assignments = []
        role_names = [role for role, _role_ko, _categories in rp.SOURCE_ROLE_CATEGORY_POOLS]
        for index, expression in enumerate(expressions):
            sources = []
            assigned_sources = []
            for offset, role in enumerate(role_names):
                source_id = f"{index * 6 + offset + 1:03d}"
                category = f"category-{index}-{offset}"
                sources.append({"id": source_id, "name_ko": "소스", "category_ko": category})
                assigned_sources.append({"id": source_id, "role": role})
            prompt = (
                "Ultra-photorealistic live-action photography, vertical 4:5. "
                "An adult Korean Shorthair cat reacts at the peak of an extraordinary event with "
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

    def test_accepts_five_single_image_stories_with_distinct_expressions(self) -> None:
        batch, assignments = self.make_batch()
        self.assertEqual(rp.validate_story_batch(batch, assignments), [])

    def test_rejects_legacy_three_image_story(self) -> None:
        batch, assignments = self.make_batch()
        batch["stories"][0]["images"] *= 3
        errors = rp.validate_story_batch(batch, assignments)
        self.assertIn("story 1: images must contain exactly 1 item", errors)

    def test_requires_expression_phrase_inside_prompt(self) -> None:
        batch, assignments = self.make_batch()
        batch["stories"][0]["images"][0]["image_prompt"] = (
            "Ultra-photorealistic live-action photography, vertical 4:5, Korean Shorthair."
        )
        errors = rp.validate_story_batch(batch, assignments)
        self.assertIn("story 1 image 1: prompt must include hero_expression_en verbatim", errors)

    def test_requires_body_language_phrase_inside_prompt(self) -> None:
        batch, assignments = self.make_batch()
        body_language = batch["stories"][0]["hero_body_language_en"]
        prompt = batch["stories"][0]["images"][0]["image_prompt"]
        batch["stories"][0]["images"][0]["image_prompt"] = prompt.replace(body_language, "neutral pose")
        errors = rp.validate_story_batch(batch, assignments)
        self.assertIn("story 1 image 1: prompt must include hero_body_language_en verbatim", errors)

    def test_rejects_any_source_id_not_exactly_assigned(self) -> None:
        batch, assignments = self.make_batch()
        batch["stories"][0]["source_concepts"][2]["id"] = "999"
        errors = rp.validate_story_batch(batch, assignments)
        self.assertTrue(any("source_concepts ids must exactly match assigned ids" in error for error in errors))

    def test_accepts_assigned_ids_in_a_different_order(self) -> None:
        batch, assignments = self.make_batch()
        batch["stories"][0]["source_concepts"].reverse()
        self.assertEqual(rp.validate_story_batch(batch, assignments), [])

    def test_rejects_duplicate_assigned_id(self) -> None:
        batch, assignments = self.make_batch()
        batch["stories"][0]["source_concepts"][1]["id"] = batch["stories"][0]["source_concepts"][0]["id"]
        errors = rp.validate_story_batch(batch, assignments)
        self.assertTrue(any("source_concepts ids must exactly match assigned ids" in error for error in errors))


class SourceAssignmentTests(unittest.TestCase):
    def test_assigns_six_roles_per_story_without_batch_id_reuse(self) -> None:
        concepts = (ROOT / "data" / "cat_concepts_500.txt").read_text(encoding="utf-8-sig")
        with patch.object(rp, "recent_source_ids", return_value=(set(), 0)):
            first = rp.assign_source_concepts(concepts, {}, story_count=5)
            second = rp.assign_source_concepts(concepts, {}, story_count=5)

        expected_roles = [role for role, _role_ko, _categories in rp.SOURCE_ROLE_CATEGORY_POOLS]
        self.assertEqual(len(rp.parse_concepts(concepts)), 500)
        self.assertEqual(first, second)
        self.assertEqual(len(first), 5)
        self.assertTrue(all([item["role"] for item in story] == expected_roles for story in first))
        for story in first:
            for item, (_role, _role_ko, categories) in zip(story, rp.SOURCE_ROLE_CATEGORY_POOLS):
                self.assertIn(item["category_ko"], categories)
        ids = [item["id"] for story in first for item in story]
        self.assertEqual(len(ids), 30)
        self.assertEqual(len(set(ids)), 30)

    def test_compact_assignment_prompt_contains_only_assigned_concepts(self) -> None:
        concepts = (ROOT / "data" / "cat_concepts_500.txt").read_text(encoding="utf-8-sig")
        with patch.object(rp, "recent_source_ids", return_value=(set(), 0)):
            assignments = rp.assign_source_concepts(concepts, {}, story_count=5)
        compact = rp.assigned_source_prompt(assignments)

        self.assertEqual(compact.count('"story_index"'), 5)
        self.assertEqual(compact.count('"id"'), 30)
        self.assertNotIn("500. ", compact)

    def test_generation_injects_assignments_instead_of_full_library(self) -> None:
        batch, assignments = StoryBatchValidationTests().make_batch()
        response = Mock(output_text=json.dumps(batch, ensure_ascii=False))
        client = Mock()
        client.responses.create.return_value = response
        config = {"story_prompt_file": "prompts/story_generator_prompt.txt"}

        with (
            patch.object(rp, "assign_source_concepts", return_value=assignments),
            patch.object(rp, "history_for_prompt", return_value="[]"),
            patch.object(rp, "required_env", return_value="text-model"),
            patch.object(rp, "get_now", return_value=datetime(2026, 1, 2, 3, 4, 5)),
        ):
            stories = rp.generate_story_batch(client, config, "FULL_LIBRARY_SENTINEL")

        sent_prompt = client.responses.create.call_args.kwargs["input"]
        self.assertEqual(len(stories), 5)
        self.assertIn("ASSIGNED_SOURCE_CONCEPTS", sent_prompt)
        self.assertNotIn("{assigned_source_concepts}", sent_prompt)
        self.assertNotIn("FULL_LIBRARY_SENTINEL", sent_prompt)


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
