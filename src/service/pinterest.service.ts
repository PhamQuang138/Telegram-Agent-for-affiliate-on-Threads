import axios from "axios";
import { PinterestBoard } from "../types";

const PINTEREST_API = "https://api.pinterest.com/v5";

function getPinterestToken(): string {
  const token = process.env.PINTEREST_ACCESS_TOKEN;

  if (!token) {
    throw new Error("PINTEREST_ACCESS_TOKEN not found");
  }

  return token;
}

export async function getPinterestBoards(): Promise<PinterestBoard[]> {
  const res = await axios.get(`${PINTEREST_API}/boards`, {
    headers: {
      Authorization: `Bearer ${getPinterestToken()}`,
    },
  });

  const items = res.data?.items ?? [];

  return items.map((board: any) => ({
    id: board.id,
    name: board.name,
  }));
}

export async function createPinterestPin(input: {
  boardId: string;
  title: string;
  description: string;
  link: string;
  imageUrl: string;
}): Promise<string> {
  const res = await axios.post(
    `${PINTEREST_API}/pins`,
    {
      board_id: input.boardId,
      title: input.title,
      description: input.description,
      link: input.link,
      media_source: {
        source_type: "image_url",
        url: input.imageUrl,
        is_standard: true,
      },
    },
    {
      headers: {
        Authorization: `Bearer ${getPinterestToken()}`,
        "Content-Type": "application/json",
      },
    }
  );

  return res.data?.id ?? "";
}