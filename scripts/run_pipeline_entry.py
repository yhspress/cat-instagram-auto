from __future__ import annotations

import re

import run_pipeline as rp

_original_validate = rp.validate_story_batch

_PROMPT_LENGTH_ERROR = re.compile(
    r"^story \d+ image \d+: prompt length (\d+) \(must be 1\.\.1000\)$"
)


def validate_without_prompt_length_cap(
    batch: dict,
    category_map: dict[str, str],
) -> list[str]:
    """
    Keep every original validation rule except the old 1000-character upper
    limit for image_prompt. Empty prompts are still invalid.
    """
    errors = _original_validate(batch, category_map)
    filtered: list[str] = []

    for error in errors:
        match = _PROMPT_LENGTH_ERROR.match(error)
        if match:
            length = int(match.group(1))
            if length > 1000:
                print(f"[INFO] prompt length cap disabled; accepting {length} chars")
                continue
        filtered.append(error)

    return filtered


rp.validate_story_batch = validate_without_prompt_length_cap

if __name__ == "__main__":
    rp.main()
