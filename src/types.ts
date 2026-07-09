export type PinStep =
  | "idle"
  | "waiting_photo"
  | "waiting_link"
  | "waiting_board"
  | "waiting_confirm";

export interface PinterestBoard {
  id: string;
  name: string;
}

export interface PinSession {
  step: PinStep;
  telegramFileId?: string;
  teepublicUrl?: string;
  uploadedImageUrl?: string;
  boardId?: string;
  boardName?: string;
  title?: string;
  description?: string;
}