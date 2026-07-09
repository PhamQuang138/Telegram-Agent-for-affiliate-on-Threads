import fs from "node:fs";
import path from "node:path";
import { DraftContent, getPreviousSimilarPosts } from "../service/threadsRepository.service";
import { generateWithRetry } from "../service/gemini";

export type ThreadsShopeeStyle =
  | "natural"
  | "funny"
  | "review"
  | "problem_solution"
  | "trend";

export interface ThreadsShopeeInput {
  keyword: string;
  product_name?: string;
  affiliate_url?: string;
  style?: ThreadsShopeeStyle;
}

const MODEL = "gemini-2.5-flash-lite";

function loadPrompt(): string {
  const promptPath = path.resolve(process.cwd(), "prompts", "threads_shopee_prompt.txt");
  return fs.readFileSync(promptPath, "utf8");
}

function cleanJson(raw: string): string {
  return raw.replace(/```json/gi, "").replace(/```/g, "").trim();
}

function normalizeHashtag(tag: string): string {
  return tag
    .replace(/^#+/, "")
    .trim()
    .replace(/\s+/g, "")
    .slice(0, 32);
}

function fallbackDraft(input: ThreadsShopeeInput): DraftContent {
  const keyword = input.keyword || input.product_name || "món đồ Shopee";
  const content = `Mình vừa note lại ${keyword} vì khá hợp cho lúc cần đồ tiện mà không muốn chọn quá lâu. Ai đang tìm món tương tự thì có thể tham khảo thêm.`;

  return {
    content: content.slice(0, 400),
    cta: "Mình để link ở bio/bình luận cho ai cần xem thêm.",
    hashtags: ["ShopeeFinds", "DoTienIch"],
    quality_score: 70,
  };
}

function validateDraft(parsed: any, input: ThreadsShopeeInput): DraftContent {
  const fallback = fallbackDraft(input);
  const content = String(parsed.content ?? fallback.content)
    .replace(input.affiliate_url ?? "", "")
    .trim()
    .slice(0, 400);

  const cta = String(parsed.cta ?? fallback.cta)
    .replace(input.affiliate_url ?? "", "")
    .trim()
    .slice(0, 160);

  const hashtags = Array.isArray(parsed.hashtags)
    ? parsed.hashtags.map((tag: unknown) => normalizeHashtag(String(tag))).filter(Boolean).slice(0, 3)
    : fallback.hashtags;

  const score = Number(parsed.quality_score ?? fallback.quality_score);

  return {
    content: content.length >= 20 ? content : fallback.content,
    cta: cta || fallback.cta,
    hashtags,
    quality_score: Number.isFinite(score) ? Math.max(0, Math.min(100, Math.round(score))) : 70,
  };
}

export async function generateThreadsShopeeDraft(input: ThreadsShopeeInput): Promise<DraftContent> {
  const keyword = input.keyword.trim();
  const productName = input.product_name?.trim() || keyword || "sản phẩm Shopee";
  const previousPosts = getPreviousSimilarPosts(keyword || productName).join("\n---\n") || "None";

  const prompt = loadPrompt()
    .replaceAll("{keyword}", keyword || productName)
    .replaceAll("{product_name}", productName)
    .replaceAll("{style}", input.style || "natural")
    .replaceAll("{previous_posts}", previousPosts);

  try {
    const raw = await generateWithRetry(prompt, MODEL);
    const parsed = JSON.parse(cleanJson(raw));
    return validateDraft(parsed, input);
  } catch (error) {
    console.error("Threads Shopee agent fallback:", error instanceof Error ? error.message : error);
    return fallbackDraft(input);
  }
}
