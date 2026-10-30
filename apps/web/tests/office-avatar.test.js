import { expect, test } from "bun:test";
import { GLTFLoader } from "three/addons/loaders/GLTFLoader.js";
import { assignOfficeAvatarModels, OFFICE_AVATAR_MODELS, OFFICE_AVATAR_POSE_BONES } from "../app/components/office-avatar.ts";

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

test("every bundled avatar contains all bones used by seated and typing poses", async () => {
  for (const model of OFFICE_AVATAR_MODELS) {
    const url = new URL(`../public${model}`, import.meta.url);
    const bytes = await Bun.file(url).arrayBuffer();
    const gltf = await new Promise((resolve, reject) => new GLTFLoader().parse(bytes, "", resolve, reject));
    const boneNames = new Set();
    gltf.scene.traverse((object) => {
      if (object.isBone) boneNames.add(object.name);
    });

    expect(Object.values(OFFICE_AVATAR_POSE_BONES).every((name) => boneNames.has(name))).toBe(true);
  }
});
