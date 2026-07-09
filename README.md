# POD Bot - Threads Shopee Affiliate Bot

POD Bot is a Python Telegram bot for creating Vietnamese Threads posts for Shopee affiliate campaigns. It can import Shopee affiliate CSV files, generate draft posts, attach 3-5 catalog links, publish to Threads, post affiliate links as replies, and track clicks through a local FastAPI redirect endpoint.

The Python/FastAPI app is the primary implementation. The older TypeScript source is still in the repository for reference, but the recommended runtime is:

```bash
python -m app.main
```

## Features

- Generate Vietnamese Threads drafts for Shopee affiliate products.
- Create engagement-only posts without product links.
- Choose engagement personas and content modes.
- Auto-match catalog links from the local database.
- Group 3-5 Shopee links into one post and one bundled reply comment.
- Import and de-duplicate Shopee affiliate CSV files.
- Queue, preview, approve, regenerate, delete, and post drafts from Telegram.
- Post to Threads through the official Threads API.
- Track clicks through `GET /go/{slug}` and store analytics in SQLite.
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

INCLUDE_TRACKING_LINK_IN_THREADS=false
POST_TRACKING_LINK_AS_REPLY=true
COMMENT_LINK_TARGET=affiliate

OPENROUTER_API_KEY=
OPENROUTER_MODEL=nvidia/nemotron-3-ultra-550b-a55b:free,cohere/north-mini-code:free,google/gemma-4-26b-a4b-it:free,nvidia/nemotron-3-super-120b-a12b:free,openai/gpt-oss-120b:free,nvidia/nemotron-3-nano-30b-a3b:free,qwen/qwen3-next-80b-a3b-instruct:free,poolside/laguna-m.1:free
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
/autodrafts [limit] [keyword]
/engagepost <topic>

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
/post <post_id>
/replylinks <post_id>
/delete <post_id>
/analytics
```

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
```

By default, the main Threads post does not include the affiliate link. The bot posts one bundled reply/comment containing 3-4 links when grouped links exist.

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
prompts/threads_shopee_prompt.txt
prompts/threads_engagement_prompt.txt
app/services/threads_repository.py
app/services/threads_service.py
app/services/shopee_csv_importer.py
```

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
#
