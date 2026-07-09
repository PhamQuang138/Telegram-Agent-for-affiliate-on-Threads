import { Context, Markup, Telegraf } from "telegraf";
import { clearSession, getSession, updateSession } from "../service/session.service";
import { uploadImageBufferToCloudinary } from "../service/cloudinary.service";
import { createPinterestPin, getPinterestBoards } from "../service/pinterest.service";
import { generatePinterestSeo } from "../service/pinterestSeo.service";

function getUserId(ctx: Context): number | null {
  return ctx.from?.id ?? null;
}

function isTeePublicUrl(text: string): boolean {
  return /^https?:\/\/(www\.)?teepublic\.com\//i.test(text.trim());
}

async function downloadTelegramFile(ctx: Context, fileId: string): Promise<Buffer> {
  const link = await ctx.telegram.getFileLink(fileId);
  const res = await fetch(link.href);

  if (!res.ok) {
    throw new Error("Cannot download Telegram image.");
  }

  const arrayBuffer = await res.arrayBuffer();
  return Buffer.from(arrayBuffer);
}

export function registerPinFlow(bot: Telegraf<Context>) {
  bot.command("pin", async (ctx) => {
    const userId = getUserId(ctx);
    if (!userId) return;

    updateSession(userId, {
      step: "waiting_photo",
      telegramFileId: undefined,
      teepublicUrl: undefined,
      boardId: undefined,
      boardName: undefined,
      title: undefined,
      description: undefined,
    });

    await ctx.reply("Gửi ảnh cần đăng lên Pinterest.");
  });

  bot.command("cancel", async (ctx) => {
    const userId = getUserId(ctx);
    if (!userId) return;

    clearSession(userId);
    await ctx.reply("Đã hủy flow đăng Pin.");
  });

  bot.on("photo", async (ctx) => {
    const userId = getUserId(ctx);
    if (!userId) return;

    const session = getSession(userId);

    if (session.step !== "waiting_photo") {
      await ctx.reply("Muốn đăng Pinterest thì gõ /pin trước.");
      return;
    }

    const photos = ctx.message.photo;
    const bestPhoto = photos[photos.length - 1];

    updateSession(userId, {
      step: "waiting_link",
      telegramFileId: bestPhoto.file_id,
    });

    await ctx.reply("Đã nhận ảnh. Giờ gửi link TeePublic của sản phẩm.");
  });

  bot.on("text", async (ctx) => {
    const userId = getUserId(ctx);
    if (!userId) return;

    const session = getSession(userId);
    const text = ctx.message.text.trim();

    if (session.step !== "waiting_link") return;

    if (!isTeePublicUrl(text)) {
      await ctx.reply("Link chưa đúng. Hãy gửi link TeePublic, ví dụ: https://www.teepublic.com/...");
      return;
    }

    updateSession(userId, {
      step: "waiting_board",
      teepublicUrl: text,
    });

    await ctx.reply("Đang lấy danh sách board Pinterest...");

    try {
      const boards = await getPinterestBoards();

      if (boards.length === 0) {
        await ctx.reply("Không tìm thấy board Pinterest nào.");
        return;
      }

      await ctx.reply(
        "Chọn board để đăng:",
        Markup.inlineKeyboard(
          boards.slice(0, 20).map((board) => [
            Markup.button.callback(board.name, `pin_board:${board.id}:${board.name}`),
          ])
        )
      );
    } catch (error) {
      console.error(error);
      await ctx.reply("Không lấy được board Pinterest. Kiểm tra PINTEREST_ACCESS_TOKEN.");
    }
  });

  bot.action(/^pin_board:(.+?):(.+)$/, async (ctx) => {
    const userId = getUserId(ctx);
    if (!userId) return;

    const session = getSession(userId);

    if (session.step !== "waiting_board") {
      await ctx.answerCbQuery("Flow không hợp lệ.");
      return;
    }

    const boardId = ctx.match[1];
    const boardName = ctx.match[2];

    updateSession(userId, {
      step: "waiting_confirm",
      boardId,
      boardName,
    });

    await ctx.answerCbQuery();
    await ctx.reply("Đang tạo title và description bằng Gemini...");

    try {
      const seo = await generatePinterestSeo(session.teepublicUrl!);

      updateSession(userId, {
        title: seo.title,
        description: seo.description,
      });

      await ctx.reply(
        `Xem trước Pin:\n\nBoard: ${boardName}\n\nTitle:\n${seo.title}\n\nDescription:\n${seo.description}`,
        Markup.inlineKeyboard([
          [Markup.button.callback("Đăng ngay", "pin_confirm")],
          [Markup.button.callback("Hủy", "pin_cancel")],
        ])
      );
    } catch (error) {
      console.error(error);
      await ctx.reply("Không tạo được SEO bằng Gemini.");
    }
  });

  bot.action("pin_cancel", async (ctx) => {
    const userId = getUserId(ctx);
    if (!userId) return;

    clearSession(userId);
    await ctx.answerCbQuery();
    await ctx.reply("Đã hủy đăng Pin.");
  });

  bot.action("pin_confirm", async (ctx) => {
    const userId = getUserId(ctx);
    if (!userId) return;

    const session = getSession(userId);

    if (
      session.step !== "waiting_confirm" ||
      !session.telegramFileId ||
      !session.teepublicUrl ||
      !session.boardId ||
      !session.title ||
      !session.description
    ) {
      await ctx.answerCbQuery("Thiếu dữ liệu.");
      return;
    }

    await ctx.answerCbQuery();
    await ctx.reply("Đang upload ảnh và đăng Pinterest...");

    try {
      const imageBuffer = await downloadTelegramFile(ctx, session.telegramFileId);
      const imageUrl = await uploadImageBufferToCloudinary(imageBuffer);

      const pinId = await createPinterestPin({
        boardId: session.boardId,
        title: session.title,
        description: session.description,
        link: session.teepublicUrl,
        imageUrl,
      });

      clearSession(userId);

      await ctx.reply(`Đăng Pin thành công.\nPin ID: ${pinId || "OK"}`);
    } catch (error) {
      console.error(error);
      await ctx.reply("Đăng Pin thất bại. Kiểm tra Cloudinary hoặc Pinterest token.");
    }
  });
}