import { expect, test } from "bun:test";
import { assignOfficeAvatarModels, OFFICE_AVATAR_MODELS } from "../app/components/office-avatar.ts";

test("registered agents get distinct deterministic avatars when the catalog has enough models", () => {
  const ids = ["agent-7", "agent-2", "agent-5", "agent-3", "agent-6", "agent-1", "agent-4"];
  const assigned = assignOfficeAvatarModels(ids);
  const reversed = assignOfficeAvatarModels([...ids].reverse());

  expect(new Set(assigned.values()).size).toBe(ids.length);
  expect(reversed).toEqual(assigned);
});

test("larger rosters reuse the avatar catalog without collapsing to one appearance", () => {
  const assigned = assignOfficeAvatarModels(Array.from({ length: 21 }, (_, index) => `agent-${index}`));
  const loads = new Map();
  for (const model of assigned.values()) loads.set(model, (loads.get(model) ?? 0) + 1);

  expect(assigned.size).toBe(21);
  expect(loads.size).toBe(OFFICE_AVATAR_MODELS.length);
  expect(Math.max(...loads.values()) - Math.min(...loads.values())).toBeLessThanOrEqual(1);
});
