from __future__ import annotations

import os
import sys
import types
import unittest
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
    def make_batch(self) -> tuple[dict, dict[str, str]]:
        category_map: dict[str, str] = {}
        expressions = [
            "wide-eyed astonishment with ears sharply forward",
            "dramatic comic hiss with ears naturally angled back",
            "intense suspicious side-eye with tense whisker pads",
            "triumphant relief with relaxed whiskers and upright tail",
            "deeply offended stare with narrowed eyes and a tail flick",
        ]
        stories = []
        for index, expression in enumerate(expressions):
            sources = []
            for offset in range(3):
                source_id = f"{index * 3 + offset + 1:03d}"
                category = f"category-{index}-{offset}"
                category_map[source_id] = category
                sources.append({"id": source_id, "name_ko": "소스", "category_ko": category})
            prompt = (
                "Ultra-photorealistic live-action photography, vertical 4:5. "
                "An adult Korean Shorthair cat reacts at the peak of an extraordinary event with "
                f"{expression}."
            )
            stories.append({
                "title_ko": f"제목 {index}",
                "hook": f"A strange event {index}",
                "caption_explanation_en": "One impossible moment stops the cat in its tracks.",
                "hashtags": ["#Cat", "#Story", "#Surprise"],
                "hero_expression_en": expression,
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
        return {"stories": stories}, category_map

    def test_accepts_five_single_image_stories_with_distinct_expressions(self) -> None:
        batch, category_map = self.make_batch()
        self.assertEqual(rp.validate_story_batch(batch, category_map), [])

    def test_rejects_legacy_three_image_story(self) -> None:
        batch, category_map = self.make_batch()
        batch["stories"][0]["images"] *= 3
        errors = rp.validate_story_batch(batch, category_map)
        self.assertIn("story 1: images must contain exactly 1 item", errors)

    def test_requires_expression_phrase_inside_prompt(self) -> None:
        batch, category_map = self.make_batch()
        batch["stories"][0]["images"][0]["image_prompt"] = (
            "Ultra-photorealistic live-action photography, vertical 4:5, Korean Shorthair."
        )
        errors = rp.validate_story_batch(batch, category_map)
        self.assertIn("story 1 image 1: prompt must include hero_expression_en verbatim", errors)


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
