import { PinSession } from "../types";

const sessions = new Map<number, PinSession>();

export function getSession(userId: number): PinSession {
  const current = sessions.get(userId);

  if (current) return current;

  const fresh: PinSession = {
    step: "idle",
  };

  sessions.set(userId, fresh);
  return fresh;
}

export function updateSession(userId: number, patch: Partial<PinSession>): PinSession {
  const current = getSession(userId);
  const next = {
    ...current,
    ...patch,
  };

  sessions.set(userId, next);
  return next;
}

export function clearSession(userId: number): void {
  sessions.delete(userId);
}