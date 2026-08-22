# 인스타 고양이 대환장극 — 1장 HERO GitHub-only 자동화

이 저장소는 첨부된 **고양이 연출 콘셉트 500개**를 소스로 사용해 OpenAI API가 10개 스토리를 생성하고, 각 스토리의 원인·절정·반전이 한눈에 읽히는 강력한 HERO 이미지 1장을 만든 뒤 Instagram 단일 이미지 게시물로 자동 게시합니다. Instagram 설명문은 각 스토리의 짧은 영어 Hook 1문장 + 영어 후킹 설명문 1문장 + 해시태그 3개로 구성됩니다.

Cloudinary는 완전히 제거했습니다. **GitHub Actions가 실행을 담당하고, 생성 이미지와 큐/이력도 GitHub 저장소에 저장하며, Instagram이 가져갈 공개 이미지 URL도 GitHub `raw.githubusercontent.com`을 사용합니다.**

## 자동 흐름

```text
500개 콘셉트
  ↓
Python이 스토리별 장소·상황·소품·반전·표정·몸짓 6개를 결정적으로 사전 배정
  ↓
스토리 큐가 비면 GPT가 10개 스토리 생성
  ↓
가장 오래된 스토리 1개 선택
  ↓
강한 표정과 사건·결과가 함께 보이는 HERO 1장 생성
  ↓
1024×1280(4:5) JPG로 정규화
  ↓
public/posts/... 에 저장
  ↓
GitHub에 먼저 commit + push
  ↓
raw.githubusercontent.com 공개 URL 확인
  ↓
Instagram image container
  ↓
Instagram publish
  ↓
published / queue / creative_history 갱신 후 commit + push
```

## 왜 게시가 2단계인가

Instagram Content Publishing API는 이미지 URL에 Meta 서버가 직접 접근할 수 있어야 합니다. 따라서 이미지 생성 직후 바로 Instagram에 보내지 않고, **먼저 GitHub에 커밋해서 공개 URL을 만든 후 게시**합니다.

게시 실패 시 `state/prepared.json`이 남기 때문에 다음 실행에서는 새 이미지를 다시 만들지 않고 준비된 HERO 이미지 1장을 우선 재사용합니다.

## 저장소 공개 설정 — 중요

기본 방식은 `raw.githubusercontent.com`을 미디어 호스트로 사용합니다. **Meta가 인증 없이 이미지를 가져가야 하므로 기본 구성에서는 GitHub 저장소가 Public이어야 합니다.**

코드 저장소를 Private으로 유지해야 한다면 별도의 Public GitHub 미디어 저장소/Pages 구성이 필요합니다. 이 패키지는 외부 Cloudinary 같은 호스팅 서비스는 사용하지 않습니다.

## 폴더 구조

```text
.github/workflows/auto_post.yml   예약/수동 GitHub Actions
config/project.json               자동화 설정
data/cat_concepts_500.txt         고양이 콘셉트 500개 원본
prompts/story_generator_prompt.txt 설정 기반 스토리/HERO 프롬프트 생성 규칙
public/posts/                     게시용 4:5 이미지와 story.json/caption.txt
scripts/run_pipeline.py           생성·큐·GitHub URL·Instagram 단일 이미지 파이프라인
scripts/check_template.py         구조 검증
state/story_queue.json            대기 스토리
state/prepared.json               GitHub 업로드 완료/게시 대기 스토리
state/creative_history.json       최근 소스·장소·소품·반전·카메라 이력
state/published.json              실제 게시 이력/슬롯 중복 방지
```

## 생성 규격

- 한 번의 스토리 리필: `batch_size` 기준, 현재 정확히 10개
- 스토리당 이미지: 정확히 1개(`HERO`)
- 구조: 원인 + 절정 행동 + 강한 고양이 표정 + 결과/반전 단서를 한 프레임에 표현
- 각 스토리 소스: Python이 사전 배정한 정확히 6개(장소·상황·소품·반전·표정·몸짓 각 1개), 배치 내 ID 중복 없음
- 모델에는 500개 전체 목록 대신 10개 스토리에 배정된 60개만 전달해 입력 토큰과 소스 선택 오류 재시도를 줄임
- Hook: 영어 50자 이하
- Caption explanation: 영어 후킹 설명문 1문장, 160자 이하
- Hashtags: 영어 정확히 3개
- 각 image prompt: 영어, 고정 글자 수 제한 없음(필수 시각 정보는 생략하지 않음)
- 고정 주인공 외형을 모든 이미지 프롬프트에 반복
- 이미지 생성 요청은 세로형으로 하고 최종 게시 파일은 1024×1280, 정확한 4:5
- 10장 HERO 이미지에서 dominant expression과 dominant gesture를 각각 최소 7종 사용하고, 표정·몸짓·카메라 조합 중복 억제 및 최근 창작 이력 반영

## 필요한 GitHub Secret

GitHub → `Settings → Secrets and variables → Actions → New repository secret`

이름:

```text
AUTOPOST_ENV
```

값 예시:

```text
OPENAI_API_KEY=...
OPENAI_TEXT_MODEL=gpt-5.4
OPENAI_IMAGE_MODEL=gpt-image-2
OPENAI_IMAGE_SIZE=1024x1536
OPENAI_IMAGE_QUALITY=medium
INSTAGRAM_USER_ID=...
INSTAGRAM_ACCESS_TOKEN=...
```

`PUBLIC_MEDIA_BASE_URL`은 기본 구성에서는 필요하지 않습니다. 비워 두면 Actions의 `GITHUB_REPOSITORY`와 현재 브랜치로 GitHub raw URL을 자동 생성합니다.

## Instagram 쪽 조건

Instagram Content Publishing API를 사용할 수 있는 계정/앱 설정과 유효한 `INSTAGRAM_USER_ID`, `INSTAGRAM_ACCESS_TOKEN`이 필요합니다. 이 파이프라인은 공개 HERO 이미지 URL과 설명문으로 단일 image container를 만든 뒤 게시합니다.

## 스케줄

기본 한국시간:

- 07:17 — slot 1
- 10:17 — slot 2
- 13:17 — slot 3
- 16:17 — slot 4
- 19:17 — slot 5

GitHub Actions의 timezone-aware schedule을 사용합니다.

## 최초 설치

1. 이 폴더 전체를 새 **Public GitHub 저장소**에 업로드합니다.
2. 저장소 `Actions`가 활성화되어 있는지 확인합니다.
3. `AUTOPOST_ENV` Secret을 등록합니다.
4. 로컬 또는 Actions에서 `python scripts/check_template.py`를 실행합니다.
5. `Actions → Cat Instagram Single Image Auto Post → Run workflow`에서 `dry-run`을 실행합니다.
6. 실행 Artifact의 `metadata.json`과 생성 HERO 이미지 1장을 확인합니다.
7. 다음에는 `publish`로 1건 실제 테스트합니다.
8. 성공하면 07:17 / 10:17 / 13:17 / 16:17 / 19:17 예약 게시가 자동으로 이어집니다.

## dry-run과 publish 차이

`dry-run`은 OpenAI API로 스토리와 이미지까지 생성하지만 GitHub에 자동 커밋하거나 Instagram에 게시하지 않습니다. 테스트 비용은 발생합니다.

`publish`는 다음 순서로 작동합니다.

1. 스토리/이미지 준비
2. `public/posts`와 준비 상태 GitHub 커밋
3. GitHub 공개 이미지 접근 확인
4. Instagram 단일 이미지 게시
5. 성공 상태/큐/창작 이력 GitHub 커밋

## 실패 복구

Instagram 단계에서 실패해도 `state/prepared.json`과 `public/posts`가 GitHub에 남습니다. 다음 예약 실행은 이 준비물을 먼저 재사용하여 같은 OpenAI 이미지 생성 비용이 다시 발생하지 않도록 설계했습니다.

강제로 기존 준비물을 버리고 새 콘텐츠를 만들 때만 수동 실행에서 `force=true`를 사용하세요.

## 중복과 장기 반복 방지

`state/published.json`은 같은 날짜/slot 중복 게시를 방지합니다.

`state/creative_history.json`은 최근 게시물의 다음 정보를 저장합니다.

- 사용한 원본 콘셉트 번호
- 중심 장소
- 핵심 소품
- 반전
- 카메라 구성

새 10개 스토리 생성 시 이 이력을 다시 GPT에 전달해 장기 반복을 줄입니다.

## 설정 변경

`config/project.json`에서 다음을 조절할 수 있습니다.

- `batch_size`: 한 번에 생성해 큐에 넣을 스토리 수(현재 10)
- `queue_refill_threshold`: 새 batch를 생성할 큐 기준
- `image_quality`: 이미지 품질
- `recent_history_limit`: GPT에 제공할 최근 게시 이력 수
- `image_publish_width` / `image_publish_height`: 기본 1024×1280 유지 권장
- `instagram_api_base`: Instagram API base URL

## 보안

API 키와 Instagram 토큰은 절대 저장소 파일에 직접 커밋하지 말고 `AUTOPOST_ENV` Secret에만 넣으세요. `.env`는 `.gitignore`에 포함되어 있습니다.
