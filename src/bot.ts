import dotenv from "dotenv";
dotenv.config();

import { Telegraf } from "telegraf";
import { registerThreadsShopeeFlow } from "./features/threadsShopee.flow";
import { generateImagePrompt, generatePodIdeas } from "./service/gemini";
import { initThreadsDatabase } from "./service/threadsRepository.service";
import { startTrackingServer } from "./service/trackingServer.service";

const token = process.env.TELEGRAM_BOT_TOKEN;

if (!token) {
  throw new Error("TELEGRAM_BOT_TOKEN not found");
}

const bot = new Telegraf(token);
initThreadsDatabase();

type SavedIdea = {
  id: number;
  niche: string;
  slogan: string;
  designConcept: string;
};

const userIdeas = new Map<number, SavedIdea[]>();

function splitMessage(text: string): string[] {
  return text.match(/[\s\S]{1,3500}/g) || [];
}

function parseIdeas(raw: string): SavedIdea[] {
  const ideaBlocks = raw
    .split(/(?=Idea\s*#?\d+)/i)
    .map((x) => x.trim())
    .filter((x) => /^Idea\s*#?\d+/i.test(x));

  return ideaBlocks.map((block, index) => {
    const niche = block.match(/Niche:\s*(.+)/i)?.[1]?.trim() || "programmer gaming";
    const slogan =
      block.match(/Slogan:\s*(.+)/i)?.[1]?.trim() ||
      block.match(/Main Slogan:\s*(.+)/i)?.[1]?.trim() ||
      "";
    const designConcept = block.match(/Design Concept:\s*(.+)/i)?.[1]?.trim() || "";

    return {
      id: index + 1,
      niche,
      slogan,
      designConcept,
    };
  });
}

bot.start((ctx) => {
  ctx.reply(`🚀 POD Bot Online

Commands:

/threads_shopee quạt mini để bàn
/threads_shopee https://s.shopee.vn/xxxx
/addlink <post_id> <shopee_affiliate_link>
/queue
/view <post_id>
/approve <post_id>
/delete <post_id>
/analytics

POD tools cũ:
/generate programmer gaming
/image 1
/image slogan | niche

Example:
/threads_shopee quạt mini để bàn`);
});

bot.command("generate", async (ctx) => {
  try {
    const userId = ctx.from.id;
    const text = ctx.message.text;
    const niche = text.replace("/generate", "").trim();

    if (!niche) {
      await ctx.reply("Nhập niche sau lệnh nhé.\n\nVí dụ:\n/generate programmer gaming");
      return;
    }

    await ctx.reply(`Đang tạo POD ideas cho niche: ${niche}`);

    const result = await generatePodIdeas(niche, 5);
    const parsedIdeas = parseIdeas(result).filter((idea) => idea.slogan || idea.designConcept);

    if (parsedIdeas.length > 0) {
      userIdeas.set(userId, parsedIdeas);
    }

    for (const chunk of splitMessage(result)) {
      await ctx.reply(chunk);
    }

    if (parsedIdeas.length > 0) {
      await ctx.reply(`Đã lưu ${parsedIdeas.length} idea. Giờ có thể dùng:\n/image 1\n/image 2\n/image 3`);
    } else {
      await ctx.reply("Bot chưa parse được idea từ kết quả. Vẫn có thể dùng format cũ:\n/image slogan | niche");
    }
  } catch (error) {
    console.error("GENERATE ERROR:", error);
    await ctx.reply(`Có lỗi khi gọi Gemini.\n${error instanceof Error ? error.message : ""}`);
  }
});

bot.command("image", async (ctx) => {
  try {
    const userId = ctx.from.id;
    const text = ctx.message.text;
    const input = text.replace("/image", "").trim();

    if (!input) {
      await ctx.reply("Dùng format:\n/image 1\n\nHoặc:\n/image slogan | niche");
      return;
    }

    let slogan = "";
    let niche = "";
    let designConcept = "";
    const ideaNumber = Number(input);

    if (Number.isFinite(ideaNumber)) {
      const ideas = userIdeas.get(userId);

      if (!ideas || ideas.length === 0) {
        await ctx.reply("Chưa có idea nào được lưu. Hãy chạy:\n/generate programmer gaming");
        return;
      }

      const idea = ideas.find((x) => x.id === ideaNumber);

      if (!idea) {
        await ctx.reply(`Không tìm thấy idea #${ideaNumber}. Hiện có ${ideas.length} idea.`);
        return;
      }

      slogan = idea.slogan;
      niche = idea.niche;
      designConcept = idea.designConcept || idea.slogan;
    } else {
      if (!input.includes("|")) {
        await ctx.reply(
          "Dùng format:\n/image 1\n\nHoặc:\n/image slogan | niche\n\nVí dụ:\n/image Achievement Unlocked Fixed The Bug | programmer gaming"
        );
        return;
      }

      const [sloganRaw, nicheRaw] = input.split("|");
      slogan = sloganRaw.trim();
      niche = nicheRaw.trim();
      designConcept = slogan;
    }

    if (!slogan && !designConcept) {
      await ctx.reply("Thiếu slogan hoặc design concept.");
      return;
    }

    if (!niche) {
      await ctx.reply("Thiếu niche.");
      return;
    }

    await ctx.reply(`Đang tạo image prompt cho:\n${slogan || designConcept}`);

    const result = await generateImagePrompt(slogan, niche, designConcept);

    for (const chunk of splitMessage(result)) {
      await ctx.reply(chunk);
    }
  } catch (error) {
    console.error("IMAGE ERROR:", error);
    await ctx.reply(`Có lỗi khi tạo image prompt.\n${error instanceof Error ? error.message : ""}`);
  }
});

registerThreadsShopeeFlow(bot);

bot.catch((err) => {
  console.error("BOT ERROR:", err);
});

const trackingServer = startTrackingServer();
bot.launch();

console.log("✅ Bot running...");

process.once("SIGINT", () => {
  trackingServer.close();
  bot.stop("SIGINT");
});

process.once("SIGTERM", () => {
  trackingServer.close();
  bot.stop("SIGTERM");
});
