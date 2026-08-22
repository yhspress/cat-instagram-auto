from pathlib import Path
import json
import re

ROOT = Path(__file__).resolve().parents[1]

required_files = [
    "config/project.json",
    "data/cat_concepts_500.txt",
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

prompt = (ROOT / config["story_prompt_file"]).read_text(encoding="utf-8")
for marker in ["{creative_history}", "{assigned_source_concepts}", "ASSIGNED_SOURCE_CONCEPTS", "Ultra-photorealistic live-action photography", "caption_explanation_en", "hero_expression_en", "hero_body_language_en", '"role": "HERO"']:
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
for marker in ["assign_source_concepts", "assigned_source_prompt", "source_concepts ids must exactly match assigned ids"]:
    if marker not in source:
        raise SystemExit(f"[ERROR] deterministic source assignment logic missing: {marker}")

print("[PASS] required files")
print("[PASS] source concepts:", len(ids))
print("[PASS] Cloudinary removed")
print("[PASS] GitHub-hosted media logic")
print("[PASS] single-image publishing logic")
print("[PASS] deterministic six-role source assignment")
print("[PASS] compact assigned-source prompt")
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
