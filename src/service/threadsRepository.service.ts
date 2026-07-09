import crypto from "node:crypto";
import path from "node:path";
import { DatabaseSync } from "node:sqlite";

export type ThreadsPostStatus =
  | "needs_link"
  | "draft"
  | "approved"
  | "posted"
  | "failed"
  | "deleted";

export interface ThreadsPost {
  id: number;
  keyword: string;
  product_name: string;
  affiliate_url: string | null;
  tracking_url: string | null;
  slug: string | null;
  content: string;
  cta: string;
  hashtags: string[];
  status: ThreadsPostStatus;
  quality_score: number;
  scheduled_at: string | null;
  threads_post_id: string | null;
  created_at: string;
  updated_at: string;
}

export interface DraftContent {
  content: string;
  cta: string;
  hashtags: string[];
  quality_score: number;
}

export interface AnalyticsSummary {
  totalPosts: number;
  draft: number;
  needsLink: number;
  approved: number;
  posted: number;
  totalClicks: number;
  topPosts: Array<{
    postId: number;
    keyword: string;
    clicks: number;
  }>;
}

function resolveSqlitePath(): string {
  const url = process.env.DATABASE_URL || "sqlite:///./affiliate_agent.db";

  if (!url.startsWith("sqlite:///")) {
    throw new Error("Only sqlite:/// DATABASE_URL is supported in this MVP.");
  }

  const rawPath = url.replace("sqlite:///", "");
  return path.resolve(process.cwd(), rawPath);
}

function nowIso(): string {
  return new Date().toISOString();
}

function mapPost(row: any): ThreadsPost {
  return {
    id: Number(row.id),
    keyword: String(row.keyword ?? ""),
    product_name: String(row.product_name ?? ""),
    affiliate_url: row.affiliate_url ?? null,
    tracking_url: row.tracking_url ?? null,
    slug: row.slug ?? null,
    content: String(row.content ?? ""),
    cta: String(row.cta ?? ""),
    hashtags: JSON.parse(String(row.hashtags ?? "[]")),
    status: row.status,
    quality_score: Number(row.quality_score ?? 0),
    scheduled_at: row.scheduled_at ?? null,
    threads_post_id: row.threads_post_id ?? null,
    created_at: String(row.created_at),
    updated_at: String(row.updated_at),
  };
}

let db: DatabaseSync | null = null;

function getDb(): DatabaseSync {
  if (db) return db;

  db = new DatabaseSync(resolveSqlitePath());
  db.exec(`
    CREATE TABLE IF NOT EXISTS threads_posts (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      keyword TEXT NOT NULL,
      product_name TEXT NOT NULL,
      affiliate_url TEXT,
      tracking_url TEXT,
      slug TEXT UNIQUE,
      content TEXT NOT NULL,
      cta TEXT NOT NULL,
      hashtags TEXT NOT NULL DEFAULT '[]',
      status TEXT NOT NULL,
      quality_score INTEGER NOT NULL DEFAULT 0,
      scheduled_at TEXT,
      threads_post_id TEXT,
      created_at TEXT NOT NULL,
      updated_at TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS click_logs (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      post_id INTEGER NOT NULL,
      slug TEXT NOT NULL,
      source TEXT NOT NULL,
      referrer TEXT,
      user_agent TEXT,
      ip_hash TEXT NOT NULL,
      clicked_at TEXT NOT NULL,
      FOREIGN KEY(post_id) REFERENCES threads_posts(id)
    );

    CREATE INDEX IF NOT EXISTS idx_threads_posts_status ON threads_posts(status);
    CREATE INDEX IF NOT EXISTS idx_click_logs_post_id ON click_logs(post_id);
    CREATE INDEX IF NOT EXISTS idx_click_logs_slug ON click_logs(slug);
  `);

  return db;
}

export function initThreadsDatabase(): void {
  getDb();
}

export function createSlug(): string {
  return crypto.randomBytes(6).toString("base64url");
}

export function createTrackingUrl(slug: string): string {
  const baseUrl = (process.env.BASE_URL || "http://localhost:8000").replace(/\/+$/, "");
  return `${baseUrl}/go/${slug}`;
}

export function hashIp(ip: string): string {
  return crypto.createHash("sha256").update(ip).digest("hex");
}

export function createThreadsPost(input: {
  keyword: string;
  productName: string;
  affiliateUrl?: string;
  draft: DraftContent;
  status: ThreadsPostStatus;
}): ThreadsPost {
  const createdAt = nowIso();
  const slug = input.affiliateUrl ? createSlug() : null;
  const trackingUrl = slug ? createTrackingUrl(slug) : null;

  const result = getDb()
    .prepare(
      `INSERT INTO threads_posts (
        keyword, product_name, affiliate_url, tracking_url, slug, content, cta,
        hashtags, status, quality_score, created_at, updated_at
      ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`
    )
    .run(
      input.keyword,
      input.productName,
      input.affiliateUrl ?? null,
      trackingUrl,
      slug,
      input.draft.content,
      input.draft.cta,
      JSON.stringify(input.draft.hashtags),
      input.status,
      input.draft.quality_score,
      createdAt,
      createdAt
    );

  return getThreadsPost(Number(result.lastInsertRowid))!;
}

export function addAffiliateLink(postId: number, affiliateUrl: string): ThreadsPost | null {
  const post = getThreadsPost(postId);

  if (!post || post.status === "deleted") {
    return null;
  }

  const slug = post.slug || createSlug();
  const trackingUrl = createTrackingUrl(slug);

  getDb()
    .prepare(
      `UPDATE threads_posts
       SET affiliate_url = ?, tracking_url = ?, slug = ?, status = ?, updated_at = ?
       WHERE id = ?`
    )
    .run(affiliateUrl, trackingUrl, slug, "draft", nowIso(), postId);

  return getThreadsPost(postId);
}

export function getThreadsPost(postId: number): ThreadsPost | null {
  const row = getDb().prepare("SELECT * FROM threads_posts WHERE id = ?").get(postId);
  return row ? mapPost(row) : null;
}

export function getThreadsPostBySlug(slug: string): ThreadsPost | null {
  const row = getDb().prepare("SELECT * FROM threads_posts WHERE slug = ?").get(slug);
  return row ? mapPost(row) : null;
}

export function listRecentThreadsPosts(limit = 10): ThreadsPost[] {
  const rows = getDb()
    .prepare("SELECT * FROM threads_posts WHERE status != 'deleted' ORDER BY id DESC LIMIT ?")
    .all(limit);

  return rows.map(mapPost);
}

export function updatePostStatus(postId: number, status: ThreadsPostStatus): ThreadsPost | null {
  const post = getThreadsPost(postId);

  if (!post) return null;

  getDb()
    .prepare("UPDATE threads_posts SET status = ?, updated_at = ? WHERE id = ?")
    .run(status, nowIso(), postId);

  return getThreadsPost(postId);
}

export function logClick(input: {
  postId: number;
  slug: string;
  referrer?: string;
  userAgent?: string;
  ip?: string;
}): void {
  getDb()
    .prepare(
      `INSERT INTO click_logs (
        post_id, slug, source, referrer, user_agent, ip_hash, clicked_at
      ) VALUES (?, ?, ?, ?, ?, ?, ?)`
    )
    .run(
      input.postId,
      input.slug,
      "threads",
      input.referrer ?? null,
      input.userAgent ?? null,
      hashIp(input.ip || ""),
      nowIso()
    );
}

export function getPreviousSimilarPosts(keyword: string, limit = 5): string[] {
  const rows = getDb()
    .prepare(
      `SELECT content FROM threads_posts
       WHERE keyword LIKE ? AND status != 'deleted'
       ORDER BY id DESC LIMIT ?`
    )
    .all(`%${keyword}%`, limit);

  return rows.map((row: any) => String(row.content));
}

export function getAnalyticsSummary(): AnalyticsSummary {
  const statusRow: any = getDb()
    .prepare(
      `SELECT
        COUNT(*) AS totalPosts,
        SUM(CASE WHEN status = 'draft' THEN 1 ELSE 0 END) AS draft,
        SUM(CASE WHEN status = 'needs_link' THEN 1 ELSE 0 END) AS needsLink,
        SUM(CASE WHEN status = 'approved' THEN 1 ELSE 0 END) AS approved,
        SUM(CASE WHEN status = 'posted' THEN 1 ELSE 0 END) AS posted
       FROM threads_posts
       WHERE status != 'deleted'`
    )
    .get();

  const clickRow: any = getDb().prepare("SELECT COUNT(*) AS totalClicks FROM click_logs").get();
  const topRows = getDb()
    .prepare(
      `SELECT p.id AS postId, p.keyword AS keyword, COUNT(c.id) AS clicks
       FROM click_logs c
       JOIN threads_posts p ON p.id = c.post_id
       GROUP BY p.id
       ORDER BY clicks DESC
       LIMIT 5`
    )
    .all();

  return {
    totalPosts: Number(statusRow.totalPosts ?? 0),
    draft: Number(statusRow.draft ?? 0),
    needsLink: Number(statusRow.needsLink ?? 0),
    approved: Number(statusRow.approved ?? 0),
    posted: Number(statusRow.posted ?? 0),
    totalClicks: Number(clickRow.totalClicks ?? 0),
    topPosts: topRows.map((row: any) => ({
      postId: Number(row.postId),
      keyword: String(row.keyword),
      clicks: Number(row.clicks),
    })),
  };
}
