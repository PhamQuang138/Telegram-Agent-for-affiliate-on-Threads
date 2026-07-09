import { GoogleGenAI } from "@google/genai";

const apiKey = process.env.GEMINI_API_KEY;

if (!apiKey) {
  throw new Error("GEMINI_API_KEY not found");
}

const ai = new GoogleGenAI({
  apiKey,
});

const MODEL = "gemini-2.5-flash-lite";

export async function generatePinterestSeo(teepublicUrl: string): Promise<{
  title: string;
  description: string;
}> {
  const prompt = `
Create Pinterest SEO content for a print-on-demand product.

Product URL:
${teepublicUrl}

Rules:
- English only
- Commercial but not spammy
- Do not use copyrighted names
- Do not use brand names
- Do not use game, anime, movie, or celebrity names
- Pinterest title max 90 characters
- Pinterest description max 450 characters
- Return only JSON

JSON format:
{
  "title": "...",
  "description": "..."
}
`;

  const response = await ai.models.generateContent({
    model: MODEL,
    contents: prompt,
  });

  const text = response.text ?? "";

  try {
    const cleaned = text
      .replace(/```json/g, "")
      .replace(/```/g, "")
      .trim();

    const parsed = JSON.parse(cleaned);

    return {
      title: String(parsed.title ?? "Original POD Gift Design").slice(0, 90),
      description: String(
        parsed.description ??
          "Original print-on-demand design for shirts, stickers, gifts, and everyday style."
      ).slice(0, 450),
    };
  } catch {
    return {
      title: "Original POD Gift Design",
      description:
        "Original print-on-demand design for shirts, stickers, gifts, and everyday style.",
    };
  }
}