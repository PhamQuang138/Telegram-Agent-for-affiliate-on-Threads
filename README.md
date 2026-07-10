# POD Bot - Threads Shopee Affiliate Bot

POD Bot is a Python Telegram bot for creating Vietnamese Threads posts for Shopee affiliate campaigns. It can import Shopee affiliate CSV files, generate draft posts, attach 3-5 catalog links, publish to Threads, post affiliate links as replies, and track clicks through a local FastAPI redirect endpoint.

The Python/FastAPI app is the primary implementation. The older TypeScript source is still in the repository for reference, but the recommended runtime is:

```bash
python -m app.main
```

## Contents

- [Features](#features)
- [Setup](#setup)
- [Run](#run)
- [Telegram Commands](#telegram-commands)
- [AI Affiliate Content Engine](#ai-affiliate-content-engine)
- [Trend Collection Engine](#trend-collection-engine)
- [Content Libraries](#content-libraries)
- [Diversity And Topic Memory](#diversity-and-topic-memory)
- [Shopee Draft Generation](#shopee-draft-generation)
- [Engagement Posts](#engagement-posts)
- [AI Provider Rotation](#ai-provider-rotation)
- [Import Shopee CSV](#import-shopee-csv)
- [Threads Posting](#threads-posting)
- [Repository Hygiene](#repository-hygiene)

## Features

- Generate Vietnamese Threads drafts for Shopee affiliate products.
- Generate drafts through a Need -> Persona -> Angle -> Story content pipeline.
- Collect trend keywords from safe local sources such as catalog data, click history, seasonality, and seed keywords.
- Create engagement-only posts without product links.
- Choose engagement personas and content modes.
- Auto-match catalog links from the local database.
- Group 3-5 Shopee links into one post and one bundled reply comment.
- Import and de-duplicate Shopee affiliate CSV files.
- Queue, preview, approve, regenerate, delete, and post drafts from Telegram.
- Post to Threads through the official Threads API.
- Track clicks through `GET /go/{slug}` and store analytics in SQLite.
- Sync official Threads metrics, replies, mentions, and keyword-search signals when token permissions allow it.
- Learn separate content strategy profiles per Threads account.
- Scan public Threads keyword-search results for purchase demand, create affiliate reply opportunities, and wait for manual approval before replying.
- Rotate AI providers/models with cooldowns for quota, rate limits, and temporary provider failures.

## Setup

Install Python dependencies:

```bash
python -m pip install -r requirements.txt
```

Create `.env` from `.env.example`:

```bash
copy .env.example .env
```

Fill in the values you need:

```env
TELEGRAM_BOT_TOKEN=

BASE_URL=http://localhost:8000
DATABASE_URL=sqlite:///./affiliate_agent.db
TRACKING_PORT=8000
PORT=8000

THREADS_ACCESS_TOKEN=
THREADS_USER_ID=
THREADS_API_BASE_URL=https://graph.threads.net/v1.0

# Optional multi-account mode
# THREADS_ACCOUNTS=acc1,acc2
# THREADS_ACC1_NAME=Main account
# THREADS_ACC1_USER_ID=
# THREADS_ACC1_ACCESS_TOKEN=
# THREADS_ACC1_PERSONA=office_minimal
# THREADS_ACC1_TOPICS=đồ văn phòng,bàn làm việc,đồ tiện ích
# THREADS_ACC2_NAME=Second account
# THREADS_ACC2_USER_ID=
# THREADS_ACC2_ACCESS_TOKEN=
# THREADS_ACC2_PERSONA=style_basic
# THREADS_ACC2_TOPICS=outfit basic,áo khoác,thời trang

INCLUDE_TRACKING_LINK_IN_THREADS=false
POST_TRACKING_LINK_AS_REPLY=true
COMMENT_LINK_TARGET=affiliate

IMPORT_EXTERNAL_THREADS_POSTS=false
THREADS_ANALYTICS_SYNC_ENABLED=true
THREADS_ANALYTICS_SYNC_INTERVAL_MINUTES=60
THREADS_REPLIES_SYNC_ENABLED=true
THREADS_KEYWORD_SEARCH_ENABLED=false
THREADS_INSIGHTS_LOOKBACK_DAYS=30
THREADS_LEARNING_MIN_POSTS=10
THREADS_AUTO_LEARN_INTERVAL_HOURS=6
THREADS_REPLY_RETENTION_DAYS=90
THREADS_KEYWORD_SAMPLE_RETENTION_DAYS=7

THREADS_DEMAND_SCANNER_ENABLED=false
THREADS_DEMAND_MIN_SCORE=70
THREADS_DEMAND_MAX_RESULTS_PER_SCAN=10
THREADS_DEMAND_MAX_APPROVE_BATCH=5
THREADS_DEMAND_MAX_REPLY_BATCH=3
THREADS_DEMAND_MAX_REPLIES_PER_ACCOUNT_PER_DAY=3
THREADS_DEMAND_REPLY_COOLDOWN_MINUTES=30
THREADS_DEMAND_OPPORTUNITY_TTL_HOURS=36
THREADS_DEMAND_MAX_LINKS_PER_COMMENT=4
THREADS_DEMAND_MANUAL_APPROVAL_REQUIRED=true

OPENROUTER_API_KEY=
OPENROUTER_MODEL=nvidia/nemotron-3-ultra-550b-a55b:free,cohere/north-mini-code:free,google/gemma-4-26b-a4b-it:free,nvidia/nemotron-3-super-120b-a12b:free,openai/gpt-oss-120b:free,nvidia/nemotron-3-nano-30b-a3b:free,qwen/qwen3-next-80b-a3b-instruct:free,poolside/laguna-m.1:free,cognitivecomputations/dolphin-mistral-24b-venice-edition:free,tencent/hy3:free
OPENROUTER_BASE_URL=https://openrouter.ai/api/v1

# Optional fallback providers
GEMINI_API_KEY=
GEMINI_MODEL=gemini-2.5-flash-lite,gemini-2.5-flash
OPENAI_API_KEY=
OPENAI_MODEL=gpt-4.1-mini
OPENAI_BASE_URL=https://api.openai.com/v1

AI_PROVIDER_ORDER=openrouter,gemini,openai
IMPORT_GENERATE_LIMIT=2
STARTUP_GENERATE_LIMIT=2

```

Notes:

- `AI_PROVIDER_ORDER` controls provider priority. The default tries OpenRouter first, then Gemini, then OpenAI.
- `GEMINI_MODEL` and `OPENROUTER_MODEL` can contain multiple comma-separated models.
- The bot cools down models that hit quota, rate limits, temporary upstream errors, missing content, or low-quality output.
- If all AI providers fail, the bot still creates a local fallback draft so the queue can keep moving.
- If `THREADS_ACCESS_TOKEN` or `THREADS_USER_ID` is missing, draft generation still works. Only `/post` requires Threads credentials.

## Run

```bash
python -m app.main
```

This starts both the Telegram bot and the FastAPI tracking server.

Tracking endpoint:

```text
GET /go/{slug}
```

Clicks are stored in SQLite. IP addresses are hashed with SHA-256 before storage, then the user is redirected to the Shopee affiliate URL.

## Telegram Commands

```text
/start
/help

/threads_shopee <keyword or Shopee affiliate link>
/contentdraft <keyword>
/autodrafts [limit] [keyword]
/engagepost <topic>
/trends
/trenddrafts [limit]
/trenddrafts <keyword>
/ideadrafts [limit] [keyword]
/performance
/ideas [keyword]
/accounts
/syncposts [account_name]
/syncinsights [account_name]
/syncreplies [account_name]
/threadstats <post_id>
/accountperformance <account_name>
/threadtrends <keyword>
/mentions [account_name]
/replysuggestions <post_id>
/scanthreads [keyword] [account_name]
/buyops [limit]
/buyop <id>
/approvebuy <id>
/approvebuybatch <id1,id2,id3>
/editbuy <id> <comment>
/skipbuy <id>
/replybuy <id> [account_name]
/replybuybatch <id1,id2,id3> [account_name]

/importcsv <csv_path> [group_size]
/updatelink <csv_path> [group_size]
/confirmupdate
/cancelupdate

/queue
/status
/modelstatus
/checkmodels [limit]
/view <post_id>
/regenerate <post_id>
/refreshdrafts [limit]

/addlink <post_id> <shopee_affiliate_link>
/approve <post_id>
/post <post_id> [account_name]
/replylinks <post_id>
/delete_thread <post_id> confirm
/delete <post_id>
/analytics
```

## AI Affiliate Content Engine

New product drafts are generated through a text-based content engine instead of writing directly from `product_name`.

Pipeline:

```text
Product scoring
Need/problem discovery
Persona selection
Angle generation
Story/post generation
Quality evaluation
Duplicate check
Analytics feedback
```

Main service files:

```text
app/services/content_engine.py
app/services/product_scoring.py
app/services/content_quality.py
app/services/content_similarity.py
app/services/hook_library.py
app/services/persona_library.py
app/services/angle_library.py
prompts/affiliate_content_engine_prompt.txt
```

Create a content-engine draft:

```text
/contentdraft quạt mini
/contentdraft áo thể thao đá bóng
```

The bot first looks for matching Shopee catalog links. If it finds 2-5 links, it creates a grouped draft and later posts those links as one reply comment. If no matching link exists, the draft is saved as `needs_link`.

Draft metadata is stored on posts when available:

```text
need
persona
angle
hook_type
story_type
target_platform
click_count
performance_score
```

`/performance` shows which persona, angle, and hook type have the best click history.

## Threads Analytics Sync

The bot can use official Threads API permissions when your account token has them:

```text
threads_manage_insights
threads_read_replies
threads_keyword_search
threads_manage_mentions
threads_delete
```

Optional permissions skip gracefully. If a token lacks a permission, posting and local click tracking still work.

Manual sync and analysis commands:

```text
/syncposts [account_name]
/syncinsights [account_name]
/syncreplies [account_name]
/threadstats <post_id>
/accountperformance <account_name>
/threadtrends <keyword>
/mentions [account_name]
/replysuggestions <post_id>
```

`/replysuggestions` only suggests replies for manual approval. The bot does not auto-reply, auto-DM, or interact with keyword-search results.

The background sync loop starts with `python -m app.main` when analytics or reply sync is enabled. It waits for `THREADS_ANALYTICS_SYNC_INTERVAL_MINUTES` before the first run and uses a lock so sync jobs do not overlap.

New metric tables:

```text
threads_post_metrics
threads_replies
threads_keyword_snapshots
account_learning_profiles
```

Keyword search stores aggregate signals only: result count, recent result count, related topics, common intents, and tone summary. It does not keep a long-term raw-post corpus.

Learning is account-specific. Each Threads account gets its own profile after it has enough posts with metric data, controlled by `THREADS_LEARNING_MIN_POSTS`.

## Purchase Demand Scanner

The MVP scanner uses the official Threads keyword search API only. It does not scrape Threads HTML and it does not auto-reply.

Enable it explicitly:

```env
THREADS_DEMAND_SCANNER_ENABLED=true
THREADS_KEYWORD_SEARCH_ENABLED=true
```

Workflow:

```text
/scanthreads
/scanthreads quạt mini
/scanthreads áo khoác nam acc1
/buyops 5
/buyop <id>
/approvebuy <id>
/replybuy <id> [account_name]
```

Batch commands require explicit IDs:

```text
/approvebuybatch 12,14,17
/replybuybatch 12,14,17 acc1
```

Safety defaults:

- Scanner is disabled by default.
- Every opportunity requires manual approval.
- Batch approve is capped by `THREADS_DEMAND_MAX_APPROVE_BATCH`.
- Batch reply is capped by `THREADS_DEMAND_MAX_REPLY_BATCH`.
- Daily reply limit per account is controlled by `THREADS_DEMAND_MAX_REPLIES_PER_ACCOUNT_PER_DAY`.
- Opportunity records expire after `THREADS_DEMAND_OPPORTUNITY_TTL_HOURS`.

Scanner flow:

```text
Threads keyword search
-> purchase-intent classification
-> Shopee catalog match
-> suggested comment with 2-4 links
-> Telegram review
-> manual approve
-> reply to Threads
```

The bot skips low-intent posts, seller spam, bought-done posts, unsafe topics, duplicates, expired opportunities, and opportunities that do not match at least one catalog product.

## Trend Collection Engine

Trend collection is implemented in:

```text
app/services/trend_service.py
```

Safe local sources currently used:

- Shopee catalog keyword frequency.
- Click history from tracked links.
- Season/calendar keywords.
- Manual seed keywords from `data/seed_keywords.txt`.
- Google Suggest suggestions from safe seed keywords.

Optional providers are stubbed safely:

- `GoogleTrendsProvider`
- `ThreadsKeywordSearchProvider`

They do not scrape HTML. If official access or permissions are missing, they skip gracefully.

Show current trend keywords:

```text
/trends
```

Generate drafts from trends:

```text
/trenddrafts 2
/trenddrafts áo khoác nam
```

Trend snapshots are cached in SQLite table `trend_snapshots` with a default 6-hour TTL so external sources are not called too often when they are enabled later.

## Content Libraries

The content engine now uses small editable JSON libraries:

```text
data/hooks.json
data/personas.json
data/angles.json
```

Hooks are grouped by type, such as `observation`, `question`, `confession`, `office_life`, `student_life`, `funny`, `problem`, `wishlist`, `minimalism`, and `seasonal`.

Personas and angles are selected from keyword, product signals, trend context, and click history. If the JSON files are missing or invalid, the bot falls back to built-in defaults.

Generate ideas without creating a draft:

```text
/ideas
/ideas quạt mini
```

The response includes suggested need, persona, angle, hook, short post ideas, and matching catalog products if available.

Generate 1-3 drafts from idea seeds:

```text
/ideadrafts
/ideadrafts 3
/ideadrafts 2 quạt mini
```

`/ideadrafts` first creates internal ideas with need/persona/angle/hook, then writes drafts from those idea seeds. `/trenddrafts` also uses idea seeds internally so trend drafts are more specific than plain keyword drafts.

## Diversity And Topic Memory

The bot stores content metadata to avoid repeating the same formula too often:

```text
persona_id
angle_id
hook
hook_type
content_type
diversity_key
```

`app/services/content_diversity.py` builds a diversity key from persona, angle, hook type, and product category. If recent posts repeat the same key too much, the content engine tries another angle or hook.

`app/services/topic_memory.py` records recently used keywords and product IDs in SQLite table `topic_memory`. `/trenddrafts` skips keywords used recently when it can. If you force `/contentdraft <keyword>`, it still creates the draft, but the engine has enough context to vary the angle.

`/performance` now also includes top keywords, products, diversity keys, bottom persona/angle/hook groups, and a short rule-based suggestion.

## Basic Workflow

1. Start the bot:

```bash
python -m app.main
```

2. In Telegram, create a Shopee draft:

```text
/threads_shopee mini desk fan
```

3. If the bot cannot find a matching catalog link, add one manually:

```text
/addlink <post_id> https://s.shopee.vn/xxxx
```

4. Preview and approve:

```text
/view <post_id>
/approve <post_id>
```

5. Post to Threads:

```text
/post <post_id>
```

6. Check analytics:

```text
/analytics
```

## Shopee Draft Generation

Create a draft from a keyword:

```text
/threads_shopee áo khoác nam
```

Create a draft directly from a Shopee affiliate link:

```text
/threads_shopee https://s.shopee.vn/xxxx
```

If the keyword matches imported catalog links, the bot automatically attaches matching links. If it finds multiple matches, it creates a grouped post with several links. If it finds no link, the draft is created with `needs_link`.

Generate new posts automatically from existing catalog links:

```text
/autodrafts
/autodrafts 2
/autodrafts 2 áo thể thao đá bóng
```

Each auto draft uses 3-5 matching catalog links when possible. The default number of posts is controlled by `IMPORT_GENERATE_LIMIT`.

## Engagement Posts

Create a non-product engagement post:

```text
/engagepost bàn làm việc bừa
/engagepost prompt viết bài Threads nghe giả
/engagepost fan bóng đá thức 3h sáng
```

The bot asks you to choose a persona:

- `Doi thuong`
- `Gay tranh cai nhe`
- `Prompt advice`

Then it asks for a content mode:

- `Cau view`
- `Cho loi khuyen`
- `Xin loi khuyen`
- `Quote/thought`
- `Observation`

Finally, it asks whether to attach random catalog links as a reply comment.

Engagement posts are saved with status `engagement`. They do not need a Shopee link and can be posted directly.

## AI Provider Rotation

The bot supports OpenRouter, Gemini, and OpenAI-compatible APIs.

Provider order:

```env
AI_PROVIDER_ORDER=openrouter,gemini,openai
```

Check the current configured models without spending quota:

```text
/modelstatus
```

Run a real lightweight model check:

```text
/checkmodels
/checkmodels 3
```

`/checkmodels` sends a tiny JSON prompt to each tested model, so it does spend requests/quota.

Model rotation behavior:

- Daily account limit on OpenRouter free models skips the remaining OpenRouter free models.
- Temporary upstream rate limits only cool down the affected model.
- Gemini quota errors cool down the affected Gemini model.
- Gemini high-demand `503` errors are treated as temporary and the bot tries another provider/model.
- Low-quality or malformed model output can trigger a short cooldown for that model.

## Import Shopee CSV

Import a Shopee Affiliate CSV from the command line:

```bash
python scripts/import_shopee_csv.py "C:\Users\duyqu\Downloads\shopee.csv"
```

Limit the number of generated drafts:

```bash
python scripts/import_shopee_csv.py "C:\Users\duyqu\Downloads\shopee.csv" --limit 5
```

Change group size:

```bash
python scripts/import_shopee_csv.py "C:\Users\duyqu\Downloads\shopee.csv" --group-size 3
python scripts/import_shopee_csv.py "C:\Users\duyqu\Downloads\shopee.csv" --group-size 6
```

Scan a new CSV from Telegram before importing:

```text
/updatelink file.csv
/updatelink file.csv 6
/confirmupdate
```

Cancel a pending CSV scan:

```text
/cancelupdate
```

`/updatelink` only scans and previews new links. Posts are created only after `/confirmupdate`. Links already in the database are skipped.

CSV columns supported:

```text
Tên sản phẩm
Link ưu đãi
```

For richer product CSV files, the importer also uses:

```text
Tên sản phẩm
Link sản phẩm
Giá
Tên cửa hàng
Link ưu đãi
```

Campaign-style CSV files can use `Tên ưu đãi`.

## Startup CSV Import

To scan a CSV automatically whenever the bot starts:

```env
STARTUP_IMPORT_CSV_PATH=shopee.csv
STARTUP_GENERATE_LIMIT=2
```

On startup, the bot scans the CSV, skips duplicate affiliate links, and generates up to `STARTUP_GENERATE_LIMIT` posts.

## Draft Refreshing

Regenerate one draft:

```text
/regenerate <post_id>
```

Keep only a small number of current drafts and regenerate them:

```text
/refreshdrafts 2
```

`/refreshdrafts` keeps the newest N draft posts, regenerates their content, and marks extra drafts as `deleted`. It does not affect `approved`, `posted`, or `engagement` posts.

## Threads Posting

The Threads API posting flow is implemented in:

```text
app/services/threads_service.py
```

Posting uses the official two-step flow:

1. Create a text container.
2. Publish the container.

Required settings:

```env
THREADS_ACCESS_TOKEN=
THREADS_USER_ID=
THREADS_API_BASE_URL=https://graph.threads.net/v1.0
```

Post a draft:

```text
/post <post_id>
/post <post_id> acc1
```

By default, the main Threads post does not include the affiliate link. The bot posts one bundled reply/comment containing 3-4 links when grouped links exist.

Multi-account posting is optional. If `THREADS_ACCOUNTS` is not set, `/post` uses the legacy `THREADS_ACCESS_TOKEN` and `THREADS_USER_ID`. If accounts are configured, `/post <post_id>` chooses a matching account from persona/topic signals and round-robin fallback. `/post <post_id> <account_name>` forces a specific account.

Show configured accounts:

```text
/accounts
```

Link settings:

```env
INCLUDE_TRACKING_LINK_IN_THREADS=false
POST_TRACKING_LINK_AS_REPLY=true
COMMENT_LINK_TARGET=affiliate
```

- `COMMENT_LINK_TARGET=affiliate` posts direct Shopee affiliate links in the reply.
- `COMMENT_LINK_TARGET=tracking` posts local tracking links from `/go/{slug}`. Use this only when `BASE_URL` is a stable public URL.

## Comment Link Bundling

For grouped posts, reply links are bundled into one comment when possible:

```text
Đây là vài món mình đã nhắc, gom lại cho mọi người dễ xem hơn.

1. Product name
https://...

2. Product name
https://...

3. Product name
https://...
```

The bot tries 4 links first, then 3 links if the comment would be too long.

## Local Files

Important files:

```text
app/main.py
app/telegram_bot.py
app/config.py
agents/threads_shopee_agent.py
app/services/content_engine.py
app/services/product_scoring.py
app/services/content_quality.py
app/services/content_similarity.py
app/services/trend_service.py
app/services/hook_library.py
app/services/persona_library.py
app/services/angle_library.py
app/services/content_diversity.py
app/services/topic_memory.py
app/services/threads_api_client.py
app/services/threads_sync_service.py
app/services/threads_insights_service.py
app/services/threads_reply_service.py
app/services/reply_analysis.py
app/services/reply_suggestion_service.py
app/services/threads_analytics_scheduler.py
app/services/purchase_intent.py
app/services/demand_product_matcher.py
app/services/demand_comment_generator.py
app/services/threads_demand_scanner.py
prompts/threads_shopee_prompt.txt
prompts/threads_engagement_prompt.txt
prompts/affiliate_content_engine_prompt.txt
app/services/threads_repository.py
app/services/threads_service.py
app/services/shopee_csv_importer.py
```

## Tests

Run the offline test suite:

```bash
python -m pytest
```

Tests cover product scoring, content quality, duplicate detection, library selection, diversity checks, content ideas, Google Suggest mocking, content-engine fallback, Threads sync/insight/reply handling, demand scanning safety, and idempotent SQLite migration.

## Repository Hygiene

The repository includes a `.gitignore` for local secrets and generated files. Do not commit:

- `.env`
- local SQLite databases such as `affiliate_agent.db`
- `node_modules/`
- Python caches such as `__pycache__/`
- local notes or agent state

Use `.env.example` for shared configuration documentation.

## Legacy TypeScript

The original TypeScript bot is still present. It can be type-checked with:

```bash
npx tsc --noEmit
```

You can also run the legacy script with:

```bash
npm run dev
```

For the current Shopee/Threads workflow, use the Python app.
