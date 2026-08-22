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


def concept_category_map(concepts: str) -> dict[str, str]:
    current = ""
    result: dict[str, str] = {}
    for raw in concepts.splitlines():
        line = raw.strip()
        m_cat = re.match(r"^\d{2}\.\s+(.+)$", line)
        if m_cat:
            current = m_cat.group(1).strip()
            continue
        m = re.match(r"^(\d{3})\.\s+", line)
        if m:
            result[m.group(1)] = current
    return result


def extract_json(text: str) -> dict:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            return json.loads(text[start:end + 1])
        raise


def validate_story_batch(batch: dict, category_map: dict[str, str]) -> list[str]:
    errors: list[str] = []
    stories = batch.get("stories")
    if not isinstance(stories, list) or len(stories) != 5:
        return ["stories must contain exactly 5 items"]

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
        if not isinstance(sources, list) or not (3 <= len(sources) <= 6):
            errors.append(f"story {si}: source_concepts must have 3..6 items")
        else:
            ids = []
            cats = set()
            for src in sources:
                sid = str(src.get("id", "")) if isinstance(src, dict) else ""
                ids.append(sid)
                if sid not in category_map:
                    errors.append(f"story {si}: unknown source id {sid!r}")
                else:
                    cats.add(category_map[sid])
            if len(set(ids)) != len(ids):
                errors.append(f"story {si}: duplicate source ids")
            if len(cats) != len(ids):
                errors.append(f"story {si}: every source concept must come from a different category")

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
                    "Korean Shorthair",
                    "4:5",
                ]
                for frag in required_fragments:
                    if frag.lower() not in prompt.lower():
                        errors.append(f"story {si} image {ii}: missing {frag!r}")
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

    if len(set(hero_expressions)) < 4:
        errors.append("stories must use at least 4 distinct hero_expression_en values")
    if len(set(hero_body_languages)) < 4:
        errors.append("stories must use at least 4 distinct hero_body_language_en values")

    return errors


def history_for_prompt(config: dict) -> str:
    data = load_json(HISTORY_PATH, {"stories": []})
    limit = int(config.get("recent_history_limit", 30))
    recent = data.get("stories", [])[-limit:]
    return json.dumps(recent, ensure_ascii=False, indent=2)


def generate_story_batch(client: OpenAI, config: dict, concepts: str) -> list[dict]:
    template = (ROOT / config["story_prompt_file"]).read_text(encoding="utf-8")
    base_prompt = template.replace("{creative_history}", history_for_prompt(config)).replace("{concepts}", concepts)
    model = required_env("OPENAI_TEXT_MODEL")
    category_map = concept_category_map(concepts)
    last_errors: list[str] = []

    for attempt in range(1, 4):
        prompt = base_prompt
        if last_errors:
            prompt += "\n\nVALIDATION_ERRORS_FROM_PREVIOUS_ATTEMPT\n" + "\n".join(f"- {e}" for e in last_errors)
            prompt += "\nRegenerate the entire JSON batch and fix every error."
        print(f"[GENERATE] story batch attempt {attempt}")
        response = client.responses.create(model=model, input=prompt)
        text = (response.output_text or "").strip()
        try:
            batch = extract_json(text)
        except Exception as exc:
            last_errors = [f"invalid JSON: {exc}"]
            continue
        last_errors = validate_story_batch(batch, category_map)
        if not last_errors:
            now = get_now(config)
            stories = batch["stories"]
            for index, story in enumerate(stories, 1):
                digest = hashlib.sha1(
                    (story["title_ko"] + story["hook"] + now.isoformat() + str(index)).encode("utf-8")
                ).hexdigest()[:8]
                story["story_id"] = f"{now.strftime('%Y%m%d%H%M%S')}-{index:02d}-{digest}"
            print("[PASS] valid 5-story batch")
            return stories
        print("[WARN] story batch validation failed:")
        for err in last_errors[:25]:
            print(" -", err)

    raise SystemExit("[ERROR] story generation failed validation after 3 attempts\n" + "\n".join(last_errors))


def queue_data() -> dict:
    return load_json(QUEUE_PATH, {"stories": []})


def ensure_queue(client: OpenAI, config: dict, concepts: str) -> dict:
    queue = queue_data()
    threshold = int(config.get("queue_refill_threshold", 1))
    if len(queue.get("stories", [])) >= threshold:
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
    model = required_env("OPENAI_IMAGE_MODEL")
    size = os.environ.get("OPENAI_IMAGE_SIZE", config.get("image_generate_size", "1024x1536"))
    quality = os.environ.get("OPENAI_IMAGE_QUALITY", config.get("image_quality", "medium"))
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
