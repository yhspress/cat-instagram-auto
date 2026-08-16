from __future__ import annotations

import json

from openai import OpenAI

import run_pipeline as rp

_original_validate = rp.validate_story_batch


def _repair_overlong_prompts(batch: dict) -> None:
    targets = []
    for si, story in enumerate(batch.get("stories", []), 1):
        for ii, image in enumerate(story.get("images", []), 1):
            prompt = str(image.get("image_prompt", ""))
            if len(prompt) > 1000:
                targets.append({
                    "story": si,
                    "image": ii,
                    "role": image.get("role", ""),
                    "image_prompt": prompt,
                })

    if not targets:
        return

    print(f"[REPAIR] compressing {len(targets)} prompts over 1000 chars")
    client = OpenAI(api_key=rp.required_env("OPENAI_API_KEY"))
    model = rp.required_env("OPENAI_TEXT_MODEL")
    instruction = f"""
Return JSON only. Compress every supplied image_prompt to 760-880 characters including spaces and punctuation; HARD MAXIMUM 930.
Do not change story/image numbers. Preserve complete cat appearance, scene event/continuity, camera/lens, props/supporting characters, safety, realism, and exclusions.
Every result must contain the exact strings "Ultra-photorealistic live-action photography", "Korean Shorthair", and "4:5".
Use compact semicolon/slash phrasing rather than removing required facts.
Return exactly: {{"prompts":[{{"story":1,"image":1,"image_prompt":"..."}}]}}
INPUT:
{json.dumps({"prompts": targets}, ensure_ascii=False)}
"""
    response = client.responses.create(model=model, input=instruction)
    repaired = rp.extract_json((response.output_text or "").strip())

    for item in repaired.get("prompts", []):
        try:
            si = int(item["story"]) - 1
            ii = int(item["image"]) - 1
            prompt = str(item["image_prompt"]).strip()
            if not (0 <= si < len(batch["stories"])):
                continue
            if not (0 <= ii < len(batch["stories"][si]["images"])):
                continue
            batch["stories"][si]["images"][ii]["image_prompt"] = prompt
            print(f"[REPAIR] story {si + 1} image {ii + 1}: {len(prompt)} chars")
        except (KeyError, TypeError, ValueError, IndexError):
            continue


def validate_with_repair(batch: dict, category_map: dict[str, str]) -> list[str]:
    _repair_overlong_prompts(batch)
    return _original_validate(batch, category_map)


rp.validate_story_batch = validate_with_repair

if __name__ == "__main__":
    rp.main()
