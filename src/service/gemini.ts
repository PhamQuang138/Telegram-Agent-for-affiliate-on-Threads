import dotenv from "dotenv";
dotenv.config();

import { GoogleGenAI } from "@google/genai";

let ai: GoogleGenAI | null = null;

function getAi(): GoogleGenAI {
  if (ai) return ai;

  const apiKey = process.env.GEMINI_API_KEY;

  if (!apiKey) {
    throw new Error("GEMINI_API_KEY not found");
  }

  ai = new GoogleGenAI({
    apiKey,
  });

  return ai;
}

const FAST_MODEL = "gemini-2.5-flash-lite";
const QUALITY_MODEL = "gemini-2.5-flash-lite";

const POD_SYSTEM_PROMPT = `
You are an expert Print-on-Demand (POD) business assistant for Redbubble, TeePublic, Merch by Amazon, Etsy, and Shopify.

Your goal is to create commercially viable POD product ideas.

Rules:
- Never use copyrighted content
- Never use trademarked phrases
- Never use brand names
- Never use company names
- Never use game titles
- Never use anime titles
- Never use movie titles
- Never use celebrity names
- Generate only original concepts
- Focus on buyer intent
- Focus on evergreen demand
- Focus on Redbubble compatibility
`;

export async function generateWithRetry(
  prompt: string,
  model: string,
  retries = 2
): Promise<string> {
  for (let attempt = 1; attempt <= retries; attempt++) {
    try {
      console.log(
        `Gemini request attempt ${attempt}/${retries} - model: ${model}`
      );

      const response = await getAi().models.generateContent({
        model,
        contents: prompt,
      });

      return response.text ?? "No response generated.";
    } catch (error: any) {
      const status = error?.status;
      console.error(`Gemini attempt ${attempt} failed. Status:`, status);

      if ((status === 503 || status === 429) && attempt < retries) {
        const delayMs = 5000;
        await new Promise((resolve) => setTimeout(resolve, delayMs));
        continue;
      }

      if (status === 503) {
        return "Gemini đang quá tải. Hãy thử lại sau vài phút.";
      }

      if (status === 429) {
        return "Bạn đã chạm quota Gemini API free tier. Hãy giảm số lượng idea hoặc thử lại sau.";
      }

      throw error;
    }
  }

  return "Gemini không phản hồi.";
}

export async function generatePodIdeas(
  niche: string,
  count: number = 5
): Promise<string> {
  const prompt = `
${POD_SYSTEM_PROMPT}

Generate ${count} POD design ideas.

Niche:
${niche}

IMPORTANT:
Return ONLY plain text.
Do NOT use markdown.
Do NOT use tables.
Do NOT use bullet points.
Do NOT use bold text.
Do NOT add extra explanation.

Use EXACTLY this format for every idea:

Idea #1
Niche: ${niche}
Target Audience: ...
Slogan: ...
Design Concept: ...
SEO Title: ...
Tags: tag1, tag2, tag3, tag4, tag5, tag6, tag7, tag8, tag9, tag10, tag11, tag12, tag13, tag14, tag15
Short Description: ...

Idea #2
Niche: ${niche}
Target Audience: ...
Slogan: ...
Design Concept: ...
SEO Title: ...
Tags: tag1, tag2, tag3, tag4, tag5, tag6, tag7, tag8, tag9, tag10, tag11, tag12, tag13, tag14, tag15
Short Description: ...

Rules:
- English only
- Original ideas
- Redbubble friendly
- Commercially viable
- Buyer focused
- Exactly 15 tags per idea
`;

  return generateWithRetry(prompt, FAST_MODEL);
}

export async function generateImagePrompt(
  slogan: string,
  niche: string,
  concept?: string
): Promise<string> {
  const prompt = `
You are creating a prompt for Gemini Web image generation.

Create ONE image prompt for a commercial print-on-demand design.

Niche:
${niche}

Core concept:
${concept || slogan}

Optional text/slogan:
${slogan}

CRITICAL RULES:
- Do NOT mention transparent background.
- Do NOT mention PNG.
- Do NOT mention 300 DPI.
- Do NOT mention 6000x6000.
- Do NOT mention canvas size.
- Do NOT mention safe margin.
- Do NOT mention apparel mockup.
- Do NOT mention t-shirt preview.
- Do NOT mention hoodie preview.
- Do NOT mention product preview.
- Do NOT mention human model unless the concept specifically needs a character.
- Do NOT use copyrighted characters.
- Do NOT use brand logos.
- Do NOT use game titles.
- Do NOT use anime titles.
- Do NOT use celebrity likeness.

BACKGROUND RULES:
- Use exactly ONE solid background color.
- Choose one background color from: black, white, dark navy, dark green, cream, beige, light gray.
- No transparent background.
- No gradients.
- No complex background scene.

DESIGN FREEDOM:
- Do NOT force typography-only.
- The design may be character-based, mascot-based, illustration-based, object-based, emblem-based, or text-based.
- Choose the most commercially attractive POD composition.
- The slogan may be main focus, secondary element, or very small.
- Make it look like a finished printable design, not a rough concept.

ALLOWED ART STYLES:
- Cute cartoon mascot
- Funny cartoon character
- Chibi-style original character
- Kawaii animal illustration
- Retro rubber-hose cartoon style
- Vintage comic illustration
- Pixel art
- Gaming-inspired original item design
- Fantasy creature illustration
- Dark cute illustration
- Minimal vector icon
- Bold typography design
- Retro badge design
- Sticker-style artwork
- Graffiti-style illustration
- Hand-drawn doodle style
- Pop art style
- Flat vector illustration
- 90s cartoon-inspired original style
- Cozy whimsical illustration
- Cyberpunk-inspired original character
- Cute monster design
- Original animal mascot
- Funny food character
- Skull/cartoon skull design if suitable
- Robot mascot design if suitable

STYLE RULES:
- Clean commercial illustration.
- Strong contrast.
- Solid colors preferred.
- Use 2 to 5 colors maximum.
- No photorealism.
- No watercolor.
- No messy details.
- No copyrighted visual references.
- Make the design readable and suitable for shirts, stickers, posters, and POD products.

OUTPUT:
Return only the final image prompt as one paragraph.
Do not add labels.
Do not explain.
`;

  return generateWithRetry(prompt, QUALITY_MODEL);
}
