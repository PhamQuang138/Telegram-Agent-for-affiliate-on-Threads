import { Context, Telegraf } from "telegraf";
import { generateThreadsShopeeDraft } from "../agents/threads_shopee_agent";
import {
  addAffiliateLink,
  createThreadsPost,
  getAnalyticsSummary,
  getThreadsPost,
  listRecentThreadsPosts,
  ThreadsPost,
  updatePostStatus,
} from "../service/threadsRepository.service";

function commandText(ctx: Context): string {
  const message = ctx.message;
  return message && "text" in message ? message.text : "";
}

function commandArg(ctx: Context, command: string): string {
  return commandText(ctx).replace(new RegExp(`^/${command}(?:@\\w+)?`, "i"), "").trim();
}

function isShopeeLink(text: string): boolean {
  try {
    const url = new URL(text.trim());
    return /(^|\.)shopee\.(vn|com)$/i.test(url.hostname) || /^s\.shopee\.vn$/i.test(url.hostname);
  } catch {
    return false;
  }
}

function inferKeyword(input: string): string {
  if (!isShopeeLink(input)) return input.trim();
  return "sản phẩm Shopee";
}

function formatHashtags(tags: string[]): string {
  return tags.map((tag) => `#${tag.replace(/^#+/, "")}`).join(" ");
}

function formatPreview(post: ThreadsPost): string {
  return `🧵 Threads Shopee Draft #${post.id}

Nội dung:
${post.content}

CTA:
${post.cta}

Hashtags:
${formatHashtags(post.hashtags)}

Tracking link:
${post.tracking_url || "chưa có link Shopee"}

Status:
${post.status}

Lệnh:
- /addlink ${post.id} <link>
- /approve ${post.id}
- /view ${post.id}
- /delete ${post.id}`;
}

function parsePostId(raw: string): number | null {
  const postId = Number(raw.trim().split(/\s+/)[0]);
  return Number.isInteger(postId) && postId > 0 ? postId : null;
}

export function registerThreadsShopeeFlow(bot: Telegraf<Context>) {
  bot.command("help", async (ctx) => {
    await ctx.reply(`Commands:
/threads_shopee <keyword hoặc Shopee affiliate link>
/addlink <post_id> <shopee_affiliate_link>
/queue
/view <post_id>
/approve <post_id>
/delete <post_id>
/analytics

Đăng Threads hiện là thủ công: bot chuẩn bị nội dung, tracking link và queue để bạn copy đăng.`);
  });

  bot.command("threads_shopee", async (ctx) => {
    const input = commandArg(ctx, "threads_shopee");

    if (!input) {
      await ctx.reply("Dùng: /threads_shopee <keyword hoặc Shopee affiliate link>");
      return;
    }

    await ctx.reply("Đang tạo draft Threads Shopee...");

    const affiliateUrl = isShopeeLink(input) ? input : undefined;
    const keyword = inferKeyword(input);
    const productName = affiliateUrl ? "sản phẩm Shopee" : keyword;
    const draft = await generateThreadsShopeeDraft({
      keyword,
      product_name: productName,
      affiliate_url: affiliateUrl,
      style: "natural",
    });

    const post = createThreadsPost({
      keyword,
      productName,
      affiliateUrl,
      draft,
      status: affiliateUrl ? "draft" : "needs_link",
    });

    await ctx.reply(formatPreview(post));

    if (!affiliateUrl) {
      await ctx.reply(`Draft #${post.id} đang thiếu link. Gửi: /addlink ${post.id} <shopee_affiliate_link>`);
    }
  });

  bot.command("addlink", async (ctx) => {
    const input = commandArg(ctx, "addlink");
    const [postIdRaw, linkRaw] = input.split(/\s+/, 2);
    const postId = Number(postIdRaw);
    const link = linkRaw?.trim();

    if (!Number.isInteger(postId) || !link || !isShopeeLink(link)) {
      await ctx.reply("Dùng: /addlink <post_id> <shopee_affiliate_link>");
      return;
    }

    const post = addAffiliateLink(postId, link);

    if (!post) {
      await ctx.reply(`Không tìm thấy post #${postId}.`);
      return;
    }

    await ctx.reply(formatPreview(post));
  });

  bot.command("queue", async (ctx) => {
    const posts = listRecentThreadsPosts(10);

    if (posts.length === 0) {
      await ctx.reply("Queue đang trống.");
      return;
    }

    await ctx.reply(
      posts.map((post) => `#${post.id} | ${post.status} | ${post.keyword} | score ${post.quality_score}`).join("\n")
    );
  });

  bot.command("view", async (ctx) => {
    const postId = parsePostId(commandArg(ctx, "view"));

    if (!postId) {
      await ctx.reply("Dùng: /view <post_id>");
      return;
    }

    const post = getThreadsPost(postId);

    if (!post || post.status === "deleted") {
      await ctx.reply(`Không tìm thấy post #${postId}.`);
      return;
    }

    await ctx.reply(formatPreview(post));
  });

  bot.command("approve", async (ctx) => {
    const postId = parsePostId(commandArg(ctx, "approve"));

    if (!postId) {
      await ctx.reply("Dùng: /approve <post_id>");
      return;
    }

    const current = getThreadsPost(postId);

    if (!current || current.status === "deleted") {
      await ctx.reply(`Không tìm thấy post #${postId}.`);
      return;
    }

    if (!current.affiliate_url || !current.tracking_url) {
      await ctx.reply(`Post #${postId} chưa có link Shopee. Dùng /addlink ${postId} <link> trước.`);
      return;
    }

    const post = updatePostStatus(postId, "approved")!;
    await ctx.reply(`Đã approve post #${post.id}. Copy nội dung ở /view ${post.id} để đăng Threads thủ công.`);
  });

  bot.command("delete", async (ctx) => {
    const postId = parsePostId(commandArg(ctx, "delete"));

    if (!postId) {
      await ctx.reply("Dùng: /delete <post_id>");
      return;
    }

    const post = updatePostStatus(postId, "deleted");

    if (!post) {
      await ctx.reply(`Không tìm thấy post #${postId}.`);
      return;
    }

    await ctx.reply(`Đã xóa post #${postId} khỏi queue.`);
  });

  bot.command("analytics", async (ctx) => {
    const summary = getAnalyticsSummary();
    const top = summary.topPosts.length
      ? summary.topPosts
          .map((post, index) => `${index + 1}. #${post.postId} - ${post.clicks} clicks - ${post.keyword}`)
          .join("\n")
      : "chưa có click";

    await ctx.reply(`Tổng bài: ${summary.totalPosts}
Draft: ${summary.draft}
Needs link: ${summary.needsLink}
Approved: ${summary.approved}
Posted: ${summary.posted}
Tổng click: ${summary.totalClicks}
Top 5 bài nhiều click nhất:
${top}`);
  });
}
