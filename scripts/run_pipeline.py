from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import quote
from zoneinfo import ZoneInfo

import requests
from PIL import Image
from openai import OpenAI

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "config" / "project.json"
STATE_DIR = ROOT / "state"
QUEUE_PATH = STATE_DIR / "story_queue.json"
HISTORY_PATH = STATE_DIR / "creative_history.json"
PREPARED_PATH = STATE_DIR / "prepared.json"
PUBLISHED_PATH = STATE_DIR / "published.json"
OUTPUT = ROOT / "output"

SOURCE_ROLE_SPECS = (
    ("world", "세계", "high_concept", "worlds"),
    ("event", "사건", "high_concept", "events"),
    ("outcome", "결말", "high_concept", "outcomes"),
    ("role", "역할", "high_concept", "roles"),
    ("location", "장소", "high_concept", "locations"),
    ("prop", "소품", "high_concept", "props"),
    ("twist", "반전", "high_concept", "twists"),
    ("expression", "표정", "high_concept", "expressions"),
    ("gesture", "몸짓", "high_concept", "gestures"),
    ("visual_hook", "시각 훅", "high_concept", "visual_hooks"),
)

SUPERHERO_ARCHETYPE_RULES = {
    "HCR001": {
        "name": "red-blue agile wall-crawling urban acrobat hero",
        "style": "bright, clearly separated suit colors",
        "scene_terms": ("skyscraper", "rooftop", "vertical city", "building exterior"),
    },
    "HCR002": {
        "name": "dark caped nocturnal vigilante detective hero",
        "style": "rain-soaked, dark, weighty cinematic tone",
        "scene_terms": ("rainy rooftop", "gothic city", "shadow ambush", "criminal", "interrogation"),
    },
    "HCR003": {
        "name": "armored billionaire-tech defender hero",
        "style": "bright, clearly separated suit colors",
        "scene_terms": ("flight", "blast pose", "laboratory", "lab", "skyline combat"),
    },
    "HCR004": {
        "name": "thunder-god cosmic warrior hero",
        "style": "bright, clearly separated suit colors",
        "scene_terms": ("lightning storm", "shattered ruins", "thunder", "lightning"),
    },
    "HCR005": {
        "name": "speedster in aerodynamic bodysuit",
        "style": "bright, clearly separated suit colors",
        "scene_terms": ("motion streak", "near-frozen rescue", "high-speed rescue"),
    },
    "HCR006": {
        "name": "mystical sorcerer protector with cloak and glowing power accents",
        "style": "bright, clearly separated suit colors",
        "scene_terms": ("portal", "glowing circle", "dimensional threat"),
    },
    "HCR007": {
        "name": "patriotic shield-bearing symbol hero",
        "style": "bright, clearly separated suit colors",
        "scene_terms": ("shield", "deflect", "rescue formation"),
    },
    "HCR008": {
        "name": "Amazonian warrior protector hero",
        "style": "rain-soaked, dark, weighty cinematic tone",
        "scene_terms": ("battlefield rescue", "warrior guard", "defensive brace", "gothic city"),
    },
}

THUMBNAIL_REQUIRED_FRAGMENTS = (
    "mobile-thumbnail-readable",
    "dominant protagonist",
    "one decisive action",
)

CANONICAL_PROTAGONIST_DESCRIPTION = (
    "An attractive adult Korean Shorthair orange-and-white cheese-tabby cat with a natural, balanced, sturdy build; "
    "bright warm cheese-orange short fur with realistic darker orange tabby stripes; large natural upright ears; "
    "a broad clean white blaze running from the forehead down between the eyes toward the pink nose; clean white fur "
    "around the mouth and muzzle, on the chin, continuing naturally down the entire neck, chest and belly; all four paws "
    "and lower feet covered in clean white fur like four neat white boots, with the white fur rising naturally from the toes "
    "and clearly visible whenever the paws are shown; a distinctive small congenital orange cheese-tabby fur marking around "
    "parts of the mouth area, naturally integrated into the otherwise white muzzle, explicitly a permanent fur pattern and "
    "never food, dirt or staining; round amber-brown eyes; pink nose; short dense realistic fur; natural whiskers; slightly "
    "rounded cheeks; realistic memorable face."
)

PROTAGONIST_REQUIRED_MEANINGS = {
    "Korean Shorthair": ("korean shorthair",),
    "orange-and-white cheese-tabby": ("orange-and-white cheese-tabby", "orange cheese-tabby"),
    "white muzzle": ("white muzzle", "white fur around the mouth and muzzle"),
    "white chin": ("white chin", "on the chin"),
    "white neck": ("white neck", "entire neck"),
    "white chest": ("white chest", "chest and belly"),
    "white belly": ("white belly", "chest and belly"),
    "four natural white fur boots": ("four neat white boots", "four clearly recognizable natural white boots"),
    "white boots are fur, not clothing": ("congenital coat markings", "clean white fur like four neat white boots"),
    "amber-brown eyes": ("amber-brown eyes",),
    "pink nose": ("pink nose",),
    "permanent congenital orange mouth marking": ("permanent fur pattern", "congenital orange cheese-tabby fur marking"),
}

DEFAULT_TEXT_MODEL_PRIMARY = "gpt-5.6-luna"
DEFAULT_TEXT_MODEL_FALLBACK = "gpt-5.4-mini"
ACCOUNT_OR_AUTH_ERROR_MARKERS = (
    "insufficient_quota",
    "credit_balance_exhausted",
    "billing",
    "balance",
    "authentication",
    "invalid api key",
    "incorrect api key",
    "unauthorized",
)
RETRYABLE_API_ERROR_MARKERS = (
    "timeout",
    "timed out",
    "connection",
    "temporarily unavailable",
    "server error",
    "rate limit",
    "rate_limit",
)


def load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def required_env(key: str) -> str:
    value = os.environ.get(key, "").strip()
    if not value:
        raise SystemExit(f"[ERROR] Missing environment variable: {key}")
    return value


def load_json(path: Path, default: dict) -> dict:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def save_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def load_config() -> dict:
    return load_json(CONFIG_PATH, {})


def configured_batch_size(config: dict) -> int:
    value = int(config.get("batch_size", 10))
    if value < 1:
        raise SystemExit("[ERROR] batch_size must be at least 1")
    return value


def text_models(config: dict) -> tuple[str, str]:
    primary = os.environ.get("OPENAI_TEXT_MODEL_PRIMARY", "").strip() or config.get(
        "text_model_primary", DEFAULT_TEXT_MODEL_PRIMARY
    )
    fallback = os.environ.get("OPENAI_TEXT_MODEL_FALLBACK", "").strip() or config.get(
        "text_model_fallback", DEFAULT_TEXT_MODEL_FALLBACK
    )
    if os.environ.get("OPENAI_TEXT_MODEL", "").strip():
        print("[WARN] OPENAI_TEXT_MODEL is legacy-only and ignored for text model selection")
    return str(primary), str(fallback)


def is_account_or_auth_error(exc: Exception) -> bool:
    message = str(exc).lower()
    return any(marker in message for marker in ACCOUNT_OR_AUTH_ERROR_MARKERS)


def is_retryable_generation_error(exc: Exception) -> bool:
    status_code = getattr(exc, "status_code", None)
    if status_code in {408, 409, 429} or isinstance(status_code, int) and status_code >= 500:
        return True
    message = str(exc).lower()
    return any(marker in message for marker in RETRYABLE_API_ERROR_MARKERS)


def minimum_hero_variety(batch_size: int) -> int:
    return max(1, (batch_size * 7 + 9) // 10)


def get_now(config: dict) -> datetime:
    return datetime.now(ZoneInfo(config.get("timezone", "Asia/Seoul")))


def publication_key(now: datetime, slot: int) -> str:
    return f"{now.strftime('%Y-%m-%d')}:{slot:02d}"


def load_concepts(config: dict) -> str:
    path = ROOT / config["concepts_file"]
    if not path.exists():
        raise SystemExit(f"[ERROR] concepts file missing: {path}")
    text = path.read_text(encoding="utf-8-sig")
    ids = set(re.findall(r"(?m)^(\d{3})\.\s", text))
    if len(ids) != 500:
        raise SystemExit(f"[ERROR] expected 500 concepts, found {len(ids)}")
    return text


def parse_concepts(concepts: str) -> list[dict[str, str]]:
    current = ""
    result: list[dict[str, str]] = []
    for raw in concepts.splitlines():
        line = raw.strip()
        m_cat = re.match(r"^\d{2}\.\s+(.+)$", line)
        if m_cat:
            current = m_cat.group(1).strip()
            continue
        m = re.match(r"^(\d{3})\.\s+(.+?)(?:\s+—\s+(.+))?$", line)
        if m:
            result.append({
                "id": m.group(1),
                "name_ko": m.group(2).strip(),
                "category_ko": current,
                "concept_ko": (m.group(3) or m.group(2)).strip(),
            })
    return result


def load_high_concept_pool(config: dict) -> dict[str, list[dict[str, str]]]:
    path = ROOT / config.get("high_concept_pool_file", "data/high_concept_world_pool.json")
    if not path.exists():
        raise SystemExit(f"[ERROR] high-concept pool missing: {path}")
    raw = load_json(path, {})
    result: dict[str, list[dict[str, str]]] = {}
    required_keys = {selector for _, _, source_type, selector in SOURCE_ROLE_SPECS if source_type == "high_concept"}
    for key in required_keys:
        entries = raw.get(key)
        if not isinstance(entries, list) or not entries:
            raise SystemExit(f"[ERROR] high-concept pool {key!r} must be a non-empty list")
        parsed: list[dict[str, str]] = []
        for entry in entries:
            if not isinstance(entry, list) or len(entry) != 3 or not all(isinstance(value, str) and value.strip() for value in entry):
                raise SystemExit(f"[ERROR] high-concept pool {key!r} has an invalid entry")
            source_id, name_en, concept_en = entry
            parsed.append({
                "id": source_id,
                "name_en": name_en,
                "concept_en": concept_en,
                "category_ko": f"HIGH_CONCEPT/{key}",
            })
        if len({item["id"] for item in parsed}) != len(parsed):
            raise SystemExit(f"[ERROR] high-concept pool {key!r} contains duplicate ids")
        result[key] = parsed
    return result


def recent_source_ids(config: dict) -> tuple[set[str], int]:
    history = load_json(HISTORY_PATH, {"stories": []}).get("stories", [])
    limit = int(config.get("recent_history_limit", 30))
    recent = history[-limit:]
    used = {
        str(source_id)
        for story in recent
        for source_id in story.get("source_ids", [])
        if source_id
    }
    return used, len(history)


def assignment_signature(sources: list[dict[str, str]]) -> str:
    by_role = {str(source.get("role", "")): str(source.get("id", "")) for source in sources}
    return "|".join(f"{role}:{by_role.get(role, '')}" for role, *_rest in SOURCE_ROLE_SPECS)


def recent_axis_signatures(config: dict) -> set[str]:
    history = load_json(HISTORY_PATH, {"stories": []}).get("stories", [])
    limit = int(config.get("recent_history_limit", 30))
    return {
        str(story.get("axis_signature", ""))
        for story in history[-limit:]
        if story.get("axis_signature")
    }


def assign_source_concepts(
    concepts: str,
    config: dict,
) -> list[list[dict[str, str]]]:
    story_count = configured_batch_size(config)
    records = parse_concepts(concepts)
    high_concept_pools = load_high_concept_pool(config)
    recent_ids, history_count = recent_source_ids(config)
    used_in_batch: set[str] = set()
    assignments: list[list[dict[str, str]]] = [[] for _ in range(story_count)]

    for role_index, (role, role_ko, source_type, selector) in enumerate(SOURCE_ROLE_SPECS):
        if source_type == "high_concept":
            pool = high_concept_pools[selector]
        else:
            pool = [record for record in records if record["category_ko"] in selector]
        if len(pool) < story_count:
            raise SystemExit(f"[ERROR] not enough source concepts for role {role}: found {len(pool)}")

        start = (history_count + role_index * 11) % len(pool)
        ordered = pool[start:] + pool[:start]
        available = [item for item in ordered if item["id"] not in recent_ids]
        available.extend(item for item in ordered if item["id"] in recent_ids)

        for story_index in range(story_count):
            selected = next((item for item in available if item["id"] not in used_in_batch), None)
            if selected is None:
                raise SystemExit(f"[ERROR] no unique source concept available for role {role}")
            used_in_batch.add(selected["id"])
            assignments[story_index].append({
                "role": role,
                "role_ko": role_ko,
                **selected,
            })
            available.remove(selected)

    recent_signatures = recent_axis_signatures(config)
    outcome_index = next(index for index, spec in enumerate(SOURCE_ROLE_SPECS) if spec[0] == "outcome")
    for _ in range(story_count):
        if all(assignment_signature(sources) not in recent_signatures for sources in assignments):
            break
        outcomes = [sources[outcome_index] for sources in assignments]
        outcomes = outcomes[1:] + outcomes[:1]
        for sources, outcome in zip(assignments, outcomes):
            sources[outcome_index] = outcome
    else:
        raise SystemExit("[ERROR] unable to avoid recent axis combinations")

    return assignments


def assigned_source_prompt(assignments: list[list[dict[str, str]]]) -> str:
    payload = {
        "stories": [
            {"story_index": index, "source_concepts": sources}
            for index, sources in enumerate(assignments, 1)
        ]
    }
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def extract_json(text: str) -> dict:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError as original_error:
        start = text.find("{")
        if start < 0:
            raise original_error
        try:
            value, _end = json.JSONDecoder().raw_decode(text, start)
        except json.JSONDecodeError:
            raise original_error
        if not isinstance(value, dict):
            raise original_error
        return value


def enforce_assigned_prompt_requirements(
    batch: dict,
    assigned_sources: list[list[dict[str, str]]],
) -> None:
    stories = batch.get("stories")
    if not isinstance(stories, list) or len(stories) != len(assigned_sources):
        return
    for story, sources in zip(stories, assigned_sources):
        if not isinstance(story, dict):
            continue
        images = story.get("images")
        if not isinstance(images, list) or len(images) != 1 or not isinstance(images[0], dict):
            continue
        image = images[0]
        prompt = str(image.get("image_prompt", "")).strip()
        prompt_lower = prompt.lower()
        additions: list[str] = []
        for source in sources:
            name = str(source.get("name_en", "")).strip()
            if name and name.lower() not in prompt_lower:
                additions.append(name)

        role_source = next((source for source in sources if source.get("role") == "role"), {})
        archetype_rule = SUPERHERO_ARCHETYPE_RULES.get(str(role_source.get("id", "")))
        if archetype_rule:
            for phrase in (
                archetype_rule["name"],
                archetype_rule["style"],
                archetype_rule["scene_terms"][0],
                "no official logo, no exact emblem, no exact costume copy",
            ):
                if phrase.lower() not in prompt_lower:
                    additions.append(phrase)

        if additions:
            image["image_prompt"] = prompt + "\nRequired assigned visual details: " + "; ".join(additions) + "."


def normalize_single_hero_images(
    batch: dict,
    assigned_sources: list[list[dict[str, str]]],
) -> None:
    stories = batch.get("stories")
    if not isinstance(stories, list) or len(stories) != len(assigned_sources):
        return
    for index, (story, sources) in enumerate(zip(stories, assigned_sources), 1):
        if not isinstance(story, dict):
            continue
        images = story.get("images")
        if isinstance(images, dict):
            story["images"] = [images]
            continue
        if isinstance(images, list) and len(images) == 1 and isinstance(images[0], dict):
            continue

        expression = str(story.get("hero_expression_en", "")).strip()
        gesture = str(story.get("hero_body_language_en", "")).strip()
        hook = str(story.get("hook", "")).strip()
        assigned_names = "; ".join(
            str(source.get("name_en", "")).strip()
            for source in sources
            if str(source.get("name_en", "")).strip()
        )
        prompt = (
            "Ultra-photorealistic live-action photography, vertical 4:5, mobile-thumbnail-readable, "
            "dominant protagonist, one decisive action. "
            + CANONICAL_PROTAGONIST_DESCRIPTION
            + f" Story moment: {hook}. The cat shows {expression}; {gesture}. "
            + "Required assigned visual details: " + assigned_names + "."
        )
        story["images"] = [{
            "role": "HERO",
            "camera_strategy": f"deterministic single-HERO composition {index}",
            "image_prompt": prompt,
        }]


def validate_story_batch(
    batch: dict,
    assigned_sources: list[list[dict[str, str]]],
    config: dict,
) -> list[str]:
    errors: list[str] = []
    batch_size = configured_batch_size(config)
    minimum_variety = minimum_hero_variety(batch_size)
    stories = batch.get("stories")
    if not isinstance(stories, list) or len(stories) != batch_size:
        return [f"stories must contain exactly {batch_size} items"]
    component_count = len(SOURCE_ROLE_SPECS)
    if len(assigned_sources) != batch_size or any(len(sources) != component_count for sources in assigned_sources):
        return [f"assigned_sources must contain exactly {batch_size} stories with {component_count} items each"]

    for role, *_rest in SOURCE_ROLE_SPECS:
        role_ids: list[str] = []
        for sources in assigned_sources:
            matches = [source for source in sources if source.get("role") == role]
            if len(matches) != 1:
                return [f"assigned_sources must contain each role exactly once; invalid role {role!r}"]
            role_ids.append(str(matches[0].get("id", "")))
        if len(set(role_ids)) < minimum_variety:
            errors.append(f"assigned {role} axis must use at least {minimum_variety} distinct values")

    roles = ["HERO"]
    fingerprints: set[tuple[str, str, str]] = set()
    camera_setups: set[str] = set()
    hero_expressions: list[str] = []
    hero_body_languages: list[str] = []

    for si, story in enumerate(stories, 1):
        if not isinstance(story, dict):
            errors.append(f"story {si}: not an object")
            continue
        hook = str(story.get("hook", ""))
        if not hook or len(hook) > 50:
            errors.append(f"story {si}: hook length {len(hook)} (must be 1..50)")
        caption_expl = str(story.get("caption_explanation_en", "")).strip()
        if not caption_expl or len(caption_expl) > 160:
            errors.append(f"story {si}: caption_explanation_en length {len(caption_expl)} (must be 1..160)")
        if caption_expl:
            sentence_count = len(re.findall(r"[.!?](?:\s|$)", caption_expl))
            if sentence_count != 1:
                errors.append(f"story {si}: caption_explanation_en must be exactly 1 sentence")
        tags = story.get("hashtags")
        if not isinstance(tags, list) or len(tags) != 3 or any(not isinstance(t, str) or not t.startswith("#") for t in tags):
            errors.append(f"story {si}: hashtags must be exactly 3 #tags")

        hero_expression = str(story.get("hero_expression_en", "")).strip()
        if not hero_expression:
            errors.append(f"story {si}: hero_expression_en must not be empty")
        else:
            hero_expressions.append(hero_expression.lower())

        hero_body_language = str(story.get("hero_body_language_en", "")).strip()
        if not hero_body_language:
            errors.append(f"story {si}: hero_body_language_en must not be empty")
        else:
            hero_body_languages.append(hero_body_language.lower())

        sources = story.get("source_concepts")
        actual_ids = [
            str(source.get("id", "")) if isinstance(source, dict) else ""
            for source in sources
        ] if isinstance(sources, list) else []
        expected_ids = [source["id"] for source in assigned_sources[si - 1]]
        expected_roles_by_id = {source["id"]: source["role"] for source in assigned_sources[si - 1]}
        if len(actual_ids) != component_count or len(set(actual_ids)) != component_count or set(actual_ids) != set(expected_ids):
            errors.append(
                f"story {si}: source_concepts ids must exactly match assigned ids "
                f"{expected_ids}; found {actual_ids}"
            )
        elif any(
            str(source.get("role", "")) != expected_roles_by_id.get(str(source.get("id", "")))
            for source in sources
            if isinstance(source, dict)
        ):
            errors.append(f"story {si}: source_concepts roles must match assigned roles")

        assigned_by_role = {source["role"]: source for source in assigned_sources[si - 1]}
        assigned_expression = str(assigned_by_role.get("expression", {}).get("name_en", "")).strip()
        assigned_gesture = str(assigned_by_role.get("gesture", {}).get("name_en", "")).strip()
        assigned_event = str(assigned_by_role.get("event", {}).get("name_en", "")).strip()
        assigned_outcome = str(assigned_by_role.get("outcome", {}).get("name_en", "")).strip()
        if assigned_expression and hero_expression != assigned_expression:
            errors.append(f"story {si}: hero_expression_en must match assigned expression name_en")
        if assigned_gesture and hero_body_language != assigned_gesture:
            errors.append(f"story {si}: hero_body_language_en must match assigned gesture name_en")
        if assigned_event and assigned_outcome and assigned_event.casefold() == assigned_outcome.casefold():
            errors.append(f"story {si}: assigned event and outcome must be meaningfully different")

        images = story.get("images")
        if not isinstance(images, list) or len(images) != 1:
            errors.append(f"story {si}: images must contain exactly 1 item")
        else:
            found_roles = [str(img.get("role", "")) for img in images if isinstance(img, dict)]
            if found_roles != roles:
                errors.append(f"story {si}: image roles must be {roles}")
            for ii, img in enumerate(images, 1):
                if not isinstance(img, dict):
                    continue
                prompt = str(img.get("image_prompt", ""))
                strategy = str(img.get("camera_strategy", ""))
                if not prompt:
                    errors.append(f"story {si} image {ii}: prompt must not be empty")
                elif hero_expression and hero_expression.lower() not in prompt.lower():
                    errors.append(f"story {si} image {ii}: prompt must include hero_expression_en verbatim")
                if prompt and hero_body_language and hero_body_language.lower() not in prompt.lower():
                    errors.append(f"story {si} image {ii}: prompt must include hero_body_language_en verbatim")
                required_fragments = [
                    "Ultra-photorealistic live-action photography",
                    "4:5",
                ]
                for frag in required_fragments:
                    if frag.lower() not in prompt.lower():
                        errors.append(f"story {si} image {ii}: missing {frag!r}")
                prompt_lower = prompt.lower()
                for meaning, alternatives in PROTAGONIST_REQUIRED_MEANINGS.items():
                    if not any(alternative in prompt_lower for alternative in alternatives):
                        errors.append(f"story {si} image {ii}: missing protagonist identity meaning {meaning!r}")
                for fragment in THUMBNAIL_REQUIRED_FRAGMENTS:
                    if fragment not in prompt_lower:
                        errors.append(f"story {si} image {ii}: missing thumbnail direction {fragment!r}")
                for weak_direction in (
                    "small distant subject",
                    "ordinary front-facing shot",
                    "static front-facing portrait",
                    "blank expression",
                    "neutral pose",
                ):
                    if weak_direction in prompt_lower:
                        errors.append(f"story {si} image {ii}: weak thumbnail direction {weak_direction!r} is forbidden")
                for axis_role, assigned in assigned_by_role.items():
                    assigned_name = str(assigned.get("name_en", "")).strip()
                    if assigned_name and assigned_name.lower() not in prompt_lower:
                        errors.append(
                            f"story {si} image {ii}: prompt must include assigned {axis_role} name_en verbatim"
                        )
                role_source = assigned_by_role.get("role", {})
                archetype_rule = SUPERHERO_ARCHETYPE_RULES.get(str(role_source.get("id", "")))
                if archetype_rule:
                    if archetype_rule["name"].lower() not in prompt_lower:
                        errors.append(f"story {si} image {ii}: superhero costume-language drift from assigned archetype")
                    if archetype_rule["style"].lower() not in prompt_lower:
                        errors.append(f"story {si} image {ii}: superhero mode style language is missing")
                    if not any(term in prompt_lower for term in archetype_rule["scene_terms"]):
                        errors.append(f"story {si} image {ii}: superhero scene does not match assigned archetype")
                    if not any(
                        phrase in prompt_lower
                        for phrase in ("no official logo", "no exact emblem", "no exact costume copy")
                    ):
                        errors.append(f"story {si} image {ii}: superhero IP-safety language is missing")
                sig = strategy.strip().lower()
                if not sig:
                    errors.append(f"story {si} image {ii}: camera_strategy must not be empty")
                else:
                    if sig in camera_setups:
                        errors.append(f"story {si} image {ii}: repeated camera strategy")
                    camera_setups.add(sig)

        fp = story.get("creative_fingerprint", {})
        if isinstance(fp, dict):
            key = (
                str(fp.get("location_ko", "")).strip().lower(),
                str(fp.get("core_prop_ko", "")).strip().lower(),
                str(fp.get("twist_ko", "")).strip().lower(),
            )
            if all(key):
                if key in fingerprints:
                    errors.append(f"story {si}: repeated creative fingerprint")
                fingerprints.add(key)

    if len(set(hero_expressions)) < minimum_variety:
        errors.append(f"stories must use at least {minimum_variety} distinct hero_expression_en values")
    if len(set(hero_body_languages)) < minimum_variety:
        errors.append(f"stories must use at least {minimum_variety} distinct hero_body_language_en values")

    return errors


def history_for_prompt(config: dict) -> str:
    data = load_json(HISTORY_PATH, {"stories": []})
    limit = int(config.get("recent_history_limit", 30))
    recent = data.get("stories", [])[-limit:]
    return json.dumps(recent, ensure_ascii=False, indent=2)


def generate_story_batch(client: OpenAI, config: dict, concepts: str) -> list[dict]:
    template = (ROOT / config["story_prompt_file"]).read_text(encoding="utf-8")
    batch_size = configured_batch_size(config)
    assignments = assign_source_concepts(concepts, config)
    base_prompt = (
        template
        .replace("{batch_size}", str(batch_size))
        .replace("{minimum_hero_variety}", str(minimum_hero_variety(batch_size)))
        .replace("{protagonist_description}", CANONICAL_PROTAGONIST_DESCRIPTION)
        .replace("{creative_history}", history_for_prompt(config))
        .replace("{assigned_source_concepts}", assigned_source_prompt(assignments))
    )
    primary_model, fallback_model = text_models(config)
    print(f"[TEXT MODEL] primary={primary_model} fallback={fallback_model}")

    def generate_once(model: str) -> tuple[list[dict] | None, list[str]]:
        response = client.responses.create(model=model, input=base_prompt)
        try:
            batch = extract_json((response.output_text or "").strip())
        except Exception as exc:
            return None, [f"invalid JSON: {exc}"]
        normalize_single_hero_images(batch, assignments)
        enforce_assigned_prompt_requirements(batch, assignments)
        errors = validate_story_batch(batch, assignments, config)
        return (batch["stories"] if not errors else None), errors

    print(f"[GENERATE] using primary model {primary_model}")
    try:
        stories, errors = generate_once(primary_model)
    except Exception as exc:
        if is_account_or_auth_error(exc):
            raise SystemExit(f"[ERROR] primary text model account/auth failure: {exc}") from exc
        if not is_retryable_generation_error(exc):
            raise SystemExit(f"[ERROR] primary text model non-retryable failure: {exc}") from exc
        stories, errors = None, [f"primary API request failed: {exc}"]

    model_used = primary_model
    if stories is None:
        print("[WARN] primary model validation failed; switching to", fallback_model)
        for error in errors[:10]:
            print(" -", error)
        try:
            stories, errors = generate_once(fallback_model)
        except Exception as exc:
            if is_account_or_auth_error(exc):
                raise SystemExit(f"[ERROR] fallback text model account/auth failure: {exc}") from exc
            raise SystemExit(f"[ERROR] fallback text model request failed: {exc}") from exc
        model_used = fallback_model
        if stories is None:
            raise SystemExit("[ERROR] fallback story generation failed validation\n" + "\n".join(errors))

    now = get_now(config)
    for index, story in enumerate(stories, 1):
        digest = hashlib.sha1(
            (story["title_ko"] + story["hook"] + now.isoformat() + str(index)).encode("utf-8")
        ).hexdigest()[:8]
        story["story_id"] = f"{now.strftime('%Y%m%d%H%M%S')}-{index:02d}-{digest}"
        story["text_model_used"] = model_used
    if model_used == primary_model:
        print(f"[PASS] story batch generated with {model_used}")
    else:
        print(f"[PASS] story batch generated with fallback {model_used}")
    return stories


def queue_data() -> dict:
    return load_json(QUEUE_PATH, {"stories": []})


def queue_story_matches_active_policy(story: dict) -> bool:
    if not isinstance(story, dict):
        return False
    sources = story.get("source_concepts")
    expected_roles = {role for role, *_rest in SOURCE_ROLE_SPECS}
    if not isinstance(sources, list) or len(sources) != len(expected_roles):
        return False
    if {str(source.get("role", "")) for source in sources if isinstance(source, dict)} != expected_roles:
        return False
    images = story.get("images")
    if not isinstance(images, list) or len(images) != 1 or not isinstance(images[0], dict) or images[0].get("role") != "HERO":
        return False
    prompt = str(images[0].get("image_prompt", "")).lower()
    if not all(fragment in prompt for fragment in THUMBNAIL_REQUIRED_FRAGMENTS):
        return False
    return all(any(alternative in prompt for alternative in alternatives) for alternatives in PROTAGONIST_REQUIRED_MEANINGS.values())


def ensure_queue(client: OpenAI, config: dict, concepts: str) -> dict:
    queue = queue_data()
    existing_stories = queue.get("stories", [])
    compatible_stories = [story for story in existing_stories if queue_story_matches_active_policy(story)]
    if len(compatible_stories) != len(existing_stories):
        print(f"[QUEUE] discarded {len(existing_stories) - len(compatible_stories)} legacy-policy stories")
        queue["stories"] = compatible_stories
    threshold = int(config.get("queue_refill_threshold", 1))
    if len(queue.get("stories", [])) >= threshold:
        if len(compatible_stories) != len(existing_stories):
            save_json(QUEUE_PATH, queue)
        return queue
    new_stories = generate_story_batch(client, config, concepts)
    queue.setdefault("stories", []).extend(new_stories)
    save_json(QUEUE_PATH, queue)
    print(f"[QUEUE] added {len(new_stories)} stories; total={len(queue['stories'])}")
    return queue


def save_image_response(response, target: Path) -> None:
    item = response.data[0]
    b64 = getattr(item, "b64_json", None)
    if b64:
        target.write_bytes(base64.b64decode(b64))
        return
    url = getattr(item, "url", None)
    if url:
        r = requests.get(url, timeout=180)
        r.raise_for_status()
        target.write_bytes(r.content)
        return
    raise SystemExit("[ERROR] OpenAI image response contained no image data")


def crop_to_publish_ratio(source: Path, target: Path, width: int, height: int) -> None:
    with Image.open(source) as image:
        image = image.convert("RGB")
        src_w, src_h = image.size
        target_ratio = width / height
        src_ratio = src_w / src_h
        if src_ratio > target_ratio:
            new_w = int(src_h * target_ratio)
            left = (src_w - new_w) // 2
            image = image.crop((left, 0, left + new_w, src_h))
        else:
            new_h = int(src_w / target_ratio)
            top = (src_h - new_h) // 2
            image = image.crop((0, top, src_w, top + new_h))
        image = image.resize((width, height), Image.Resampling.LANCZOS)
        image.save(target, format="JPEG", quality=94, optimize=True)


def generate_story_images(client: OpenAI, config: dict, story: dict, public_dir: Path) -> list[str]:
    model = os.environ.get("OPENAI_IMAGE_MODEL", "").strip() or str(config.get("image_model", "gpt-image-2"))
    if model != "gpt-image-2":
        raise SystemExit(f"[ERROR] low-cost image policy requires gpt-image-2, found {model}")
    size = os.environ.get("OPENAI_IMAGE_SIZE", config.get("image_generate_size", "1024x1536"))
    quality = os.environ.get("OPENAI_IMAGE_QUALITY", config.get("image_quality", "low"))
    if quality != "low":
        raise SystemExit(f"[ERROR] low-cost image policy requires quality=low, found {quality}")
    width = int(config.get("image_publish_width", 1024))
    height = int(config.get("image_publish_height", 1280))
    public_dir.mkdir(parents=True, exist_ok=True)
    role_names = {"HERO": "01_hero"}
    paths: list[str] = []

    for img in story["images"]:
        role = img["role"]
        stem = role_names[role]
        temp = public_dir / f"{stem}_generated.png"
        final = public_dir / f"{stem}.jpg"
        prompt = img["image_prompt"]
        print(f"[GENERATE] {role} image")
        response = client.images.generate(model=model, prompt=prompt, size=size, quality=quality)
        save_image_response(response, temp)
        crop_to_publish_ratio(temp, final, width, height)
        temp.unlink(missing_ok=True)
        with Image.open(final) as check:
            if check.size != (width, height):
                raise SystemExit(f"[ERROR] wrong publish size for {final}: {check.size}")
        paths.append(str(final.relative_to(ROOT)).replace(os.sep, "/"))
        print("[PASS]", final)
    return paths


def compose_caption(story: dict, config: dict) -> str:
    hook = story["hook"].strip()
    explanation = str(story.get("caption_explanation_en", "")).strip()
    if not explanation:
        explanation = hook
    caption_parts = [hook, explanation, " ".join(story["hashtags"])]
    caption = "\n".join(part for part in caption_parts if part).strip()
    if len(caption) > int(config.get("caption_max_chars", 2200)):
        raise SystemExit("[ERROR] caption too long")
    return caption


def load_prepared() -> dict:
    return load_json(PREPARED_PATH, {})


def is_published(key: str) -> bool:
    state = load_json(PUBLISHED_PATH, {"published": []})
    return key in state.get("published", [])


def prepare(client: OpenAI, config: dict, slot: int, force: bool) -> dict:
    existing = load_prepared()
    if existing.get("story_id") and not force:
        print(f"[RECOVERY] reusing already prepared story {existing['story_id']} for {existing.get('publication_key')}")
        return existing
    if force and existing:
        print("[FORCE] replacing existing prepared story")
        save_json(PREPARED_PATH, {})

    now = get_now(config)
    key = publication_key(now, slot)
    if config.get("prevent_duplicate_slot", True) and is_published(key) and not force:
        raise SystemExit(f"[STOP] already published: {key}")

    concepts = load_concepts(config)
    queue = ensure_queue(client, config, concepts)
    if not queue.get("stories"):
        raise SystemExit("[ERROR] story queue unexpectedly empty")
    story = queue["stories"][0]

    root = Path(config.get("public_media_root", "public/posts"))
    public_dir = ROOT / root / now.strftime("%Y-%m-%d") / f"slot_{slot:02d}" / story["story_id"]
    media_paths = generate_story_images(client, config, story, public_dir)
    caption = compose_caption(story, config)
    (public_dir / "caption.txt").write_text(caption, encoding="utf-8")
    save_json(public_dir / "story.json", story)

    prepared = {
        "publication_key": key,
        "prepared_at": now.isoformat(),
        "slot": slot,
        "story_id": story["story_id"],
        "story": story,
        "caption": caption,
        "media_paths": media_paths,
        "public_dir": str(public_dir.relative_to(ROOT)).replace(os.sep, "/"),
    }
    save_json(PREPARED_PATH, prepared)
    print(f"[PREPARED] {story['story_id']} -> {key}")
    return prepared


def media_base_url() -> str:
    override = os.environ.get("PUBLIC_MEDIA_BASE_URL", "").strip().rstrip("/")
    if override:
        return override
    repo = required_env("GITHUB_REPOSITORY")
    branch = os.environ.get("MEDIA_BRANCH", "").strip() or os.environ.get("GITHUB_REF_NAME", "").strip() or "main"
    return f"https://raw.githubusercontent.com/{repo}/{quote(branch, safe='') }"


def public_media_urls(prepared: dict) -> list[str]:
    base = media_base_url()
    return [base + "/" + quote(path, safe="/") for path in prepared["media_paths"]]


def assert_public_media(urls: list[str]) -> None:
    for url in urls:
        last = None
        for _ in range(12):
            try:
                response = requests.get(url, timeout=30)
                last = response
                if response.ok and response.headers.get("content-type", "").lower().startswith("image/"):
                    print("[PASS] public media reachable:", url)
                    break
            except requests.RequestException:
                pass
            time.sleep(5)
        else:
            status = getattr(last, "status_code", "no response")
            raise SystemExit(f"[ERROR] public media not reachable ({status}): {url}\nThe GitHub repository/media URL must be publicly accessible to Meta.")


def ig_post(config: dict, path: str, data: dict) -> dict:
    base = config.get("instagram_api_base", "https://graph.instagram.com").rstrip("/")
    response = requests.post(base + path, data=data, timeout=90)
    if not response.ok:
        raise SystemExit(f"[ERROR] Instagram API POST {path}: HTTP {response.status_code}\n{response.text[:1600]}")
    return response.json()


def ig_get(config: dict, path: str, params: dict) -> dict:
    base = config.get("instagram_api_base", "https://graph.instagram.com").rstrip("/")
    response = requests.get(base + path, params=params, timeout=90)
    if not response.ok:
        raise SystemExit(f"[ERROR] Instagram API GET {path}: HTTP {response.status_code}\n{response.text[:1600]}")
    return response.json()


def wait_container(config: dict, container_id: str, token: str) -> None:
    attempts = int(config.get("instagram_container_poll_attempts", 12))
    delay = int(config.get("instagram_container_poll_seconds", 5))
    for attempt in range(1, attempts + 1):
        data = ig_get(config, f"/{container_id}", {"fields": "status_code", "access_token": token})
        status = str(data.get("status_code", "")).upper()
        if status in {"FINISHED", "PUBLISHED"}:
            return
        if status in {"ERROR", "EXPIRED"}:
            raise SystemExit(f"[ERROR] Instagram container {container_id} status={status}")
        if attempt < attempts:
            time.sleep(delay)
    raise SystemExit(f"[ERROR] Instagram container {container_id} did not finish in time")


def publish_single_image(config: dict, urls: list[str], caption: str) -> str:
    if len(urls) != 1:
        raise SystemExit(f"[ERROR] single-image post requires exactly 1 media URL, found {len(urls)}")

    user_id = required_env("INSTAGRAM_USER_ID")
    token = required_env("INSTAGRAM_ACCESS_TOKEN")
    container = ig_post(config, f"/{user_id}/media", {
        "image_url": urls[0],
        "caption": caption,
        "access_token": token,
    })
    container_id = str(container.get("id", ""))
    if not container_id:
        raise SystemExit("[ERROR] missing Instagram image container id")
    wait_container(config, container_id, token)
    print("[PASS] image container:", container_id)

    for attempt in range(1, 7):
        base = config.get("instagram_api_base", "https://graph.instagram.com").rstrip("/")
        response = requests.post(
            f"{base}/{user_id}/media_publish",
            data={"creation_id": container_id, "access_token": token},
            timeout=90,
        )
        if response.ok:
            media_id = str(response.json().get("id", ""))
            if not media_id:
                raise SystemExit("[ERROR] Instagram publish response missing media id")
            print("[PASS] Instagram image published:", media_id)
            return media_id
        if attempt < 6:
            time.sleep(5)
        else:
            raise SystemExit(f"[ERROR] Instagram media_publish failed\n{response.text[:1600]}")
    raise AssertionError("unreachable")


def finalize_success(prepared: dict, media_id: str, urls: list[str]) -> None:
    key = prepared["publication_key"]
    state = load_json(PUBLISHED_PATH, {"published": [], "posts": []})
    if key not in state.setdefault("published", []):
        state["published"].append(key)
    state["published"] = state["published"][-1000:]
    state.setdefault("posts", []).append({
        "publication_key": key,
        "story_id": prepared["story_id"],
        "media_id": media_id,
        "published_at": datetime.now(ZoneInfo("UTC")).isoformat(),
        "media_urls": urls,
    })
    state["posts"] = state["posts"][-1000:]
    save_json(PUBLISHED_PATH, state)

    queue = queue_data()
    queue["stories"] = [s for s in queue.get("stories", []) if s.get("story_id") != prepared["story_id"]]
    save_json(QUEUE_PATH, queue)

    history = load_json(HISTORY_PATH, {"stories": []})
    story = prepared["story"]
    history.setdefault("stories", []).append({
        "story_id": story["story_id"],
        "title_ko": story.get("title_ko"),
        "hook": story.get("hook"),
        "source_ids": [x.get("id") for x in story.get("source_concepts", [])],
        "axis_signature": assignment_signature(story.get("source_concepts", [])),
        "creative_fingerprint": story.get("creative_fingerprint", {}),
        "published_key": key,
    })
    history["stories"] = history["stories"][-200:]
    save_json(HISTORY_PATH, history)
    save_json(PREPARED_PATH, {})


def publish(config: dict, dry_run: bool = False) -> dict:
    prepared = load_prepared()
    if not prepared.get("story_id"):
        raise SystemExit("[ERROR] no prepared story. Run --phase prepare first.")
    urls = public_media_urls(prepared)
    if dry_run:
        print("[DRY RUN] prepared story:", prepared["story_id"])
        print("[DRY RUN] caption:\n" + prepared["caption"])
        for url in urls:
            print("[DRY RUN] media:", url)
        return prepared
    assert_public_media(urls)
    media_id = publish_single_image(config, urls, prepared["caption"])
    finalize_success(prepared, media_id, urls)
    return prepared


def write_run_metadata(prepared: dict, phase: str) -> None:
    out = OUTPUT / datetime.now().strftime("%Y%m%d_%H%M%S")
    out.mkdir(parents=True, exist_ok=True)
    save_json(out / "metadata.json", {"phase": phase, **prepared})


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--phase", choices=["prepare", "publish", "dry-run"], required=True)
    parser.add_argument("--slot", type=int, default=1)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    load_env_file(ROOT / ".env")
    config = load_config()
    if not config:
        raise SystemExit("[ERROR] config/project.json missing or invalid")

    if args.phase == "prepare":
        client = OpenAI(api_key=required_env("OPENAI_API_KEY"))
        prepared = prepare(client, config, args.slot, args.force)
        write_run_metadata(prepared, "prepare")
    elif args.phase == "publish":
        prepared = publish(config, dry_run=False)
        write_run_metadata(prepared, "publish")
    else:
        client = OpenAI(api_key=required_env("OPENAI_API_KEY"))
        prepared = prepare(client, config, args.slot, args.force)
        write_run_metadata(prepared, "dry-run")
        publish(config, dry_run=True)


if __name__ == "__main__":
    main()
