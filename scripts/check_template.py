from pathlib import Path
import json
import re

ROOT = Path(__file__).resolve().parents[1]

required_files = [
    "config/project.json",
    "data/cat_concepts_500.txt",
    "data/high_concept_world_pool.json",
    "prompts/story_generator_prompt.txt",
    "scripts/run_pipeline.py",
    ".github/workflows/auto_post.yml",
    "requirements.txt",
    "state/story_queue.json",
    "state/creative_history.json",
    "state/prepared.json",
    "state/published.json",
]

missing = [rel for rel in required_files if not (ROOT / rel).exists()]
print("=" * 72)
print("CAT INSTAGRAM GITHUB-ONLY AUTOMATION CHECK")
print("=" * 72)

if missing:
    for rel in missing:
        print("[MISSING]", rel)
    raise SystemExit(1)

config = json.loads((ROOT / "config/project.json").read_text(encoding="utf-8"))
concepts = (ROOT / config["concepts_file"]).read_text(encoding="utf-8-sig")
ids = set(re.findall(r"(?m)^(\d{3})\.\s", concepts))
if len(ids) != 500:
    raise SystemExit(f"[ERROR] source concepts: expected 500, found {len(ids)}")

high_concept_pool = json.loads((ROOT / config["high_concept_pool_file"]).read_text(encoding="utf-8"))
for key in ("worlds", "events", "outcomes", "roles", "locations", "props", "twists", "expressions", "gestures", "visual_hooks"):
    entries = high_concept_pool.get(key)
    if not isinstance(entries, list) or len(entries) < 10:
        raise SystemExit(f"[ERROR] high-concept pool {key!r} must contain at least 10 entries")
    if any(not isinstance(entry, list) or len(entry) != 3 for entry in entries):
        raise SystemExit(f"[ERROR] high-concept pool {key!r} contains an invalid entry")

prompt = (ROOT / config["story_prompt_file"]).read_text(encoding="utf-8")
for marker in ["{batch_size}", "{minimum_hero_variety}", "{creative_history}", "{assigned_source_concepts}", "ASSIGNED_SOURCE_CONCEPTS", "HIGH-CONCEPT CINEMATIC PRIORITY", "RANDOM SUPPORTING CATS", "SUPERHERO BLOCKBUSTER MODE", "outcome", "mobile-thumbnail-readable", "Ultra-photorealistic live-action photography", "caption_explanation_en", "hero_expression_en", "hero_body_language_en", '"role":"HERO"']:
    if marker not in prompt:
        raise SystemExit(f"[ERROR] story prompt missing marker: {marker}")
if "{concepts}" in prompt or "SOURCE_LIBRARY_500" in prompt:
    raise SystemExit("[ERROR] full 500-concept library must not be injected into the model prompt")

source = (ROOT / "scripts/run_pipeline.py").read_text(encoding="utf-8")
if "cloudinary" in source.lower():
    raise SystemExit("[ERROR] Cloudinary reference still exists in pipeline")
if "publish_single_image" not in source or '"image_url": urls[0]' not in source:
    raise SystemExit("[ERROR] Instagram single-image logic missing")
if "CAROUSEL" in source or "is_carousel_item" in source:
    raise SystemExit("[ERROR] legacy Instagram carousel logic still exists")
if "raw.githubusercontent.com" not in source:
    raise SystemExit("[ERROR] GitHub public media URL logic missing")
for marker in ["SOURCE_ROLE_SPECS", "load_high_concept_pool", "assign_source_concepts", "assigned_source_prompt", "source_concepts ids must exactly match assigned ids"]:
    if marker not in source:
        raise SystemExit(f"[ERROR] deterministic source assignment logic missing: {marker}")
if config.get("image_model") != "gpt-image-2" or config.get("image_quality") != "low":
    raise SystemExit("[ERROR] low-cost image policy must be gpt-image-2 with quality=low")
if config.get("text_model_primary") != "gpt-5.6-luna" or config.get("text_model_fallback") != "gpt-5.4-mini":
    raise SystemExit("[ERROR] low-cost text model routing is misconfigured")

print("[PASS] required files")
print("[PASS] source concepts:", len(ids))
print("[PASS] high-concept pool axes:", len(high_concept_pool))
print("[PASS] Cloudinary removed")
print("[PASS] GitHub-hosted media logic")
print("[PASS] single-image publishing logic")
print("[PASS] deterministic high-concept source assignment")
print("[PASS] compact assigned-source prompt")
print("[PASS] gpt-image-2 low-quality cost policy")
batch_size = int(config.get("batch_size", 0))
if batch_size < 1:
    raise SystemExit(f"[ERROR] batch_size must be at least 1, found {config.get('batch_size')}")
print(f"[PASS] configured {batch_size}-story batch")
print("[PROJECT]", config.get("project_name"))
print("[TIMEZONE]", config.get("timezone"))
print("[BATCH SIZE]", config.get("batch_size"))
print("[PUBLISH SIZE]", f"{config.get('image_publish_width')}x{config.get('image_publish_height')}")
print()
print("NEXT:")
print("1. Push this folder to a PUBLIC GitHub repository")
print("2. Add repository secret AUTOPOST_ENV from .env.example")
print("3. Run workflow_dispatch with dry-run")
print("4. Inspect artifact; then run publish once")
