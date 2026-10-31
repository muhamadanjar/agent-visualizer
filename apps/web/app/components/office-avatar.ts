export const OFFICE_AVATAR_MODELS = [
  "/assets/agents/business-man.glb",
  "/assets/agents/worker.glb",
  "/assets/agents/farmer.glb",
  "/assets/agents/punk.glb",
  "/assets/agents/beach-character.glb",
  "/assets/agents/casual-character.glb",
  "/assets/agents/hoodie-character.glb",
  "/assets/agents/Cube_Male_1.glb",
  "/assets/agents/Cube_Male_2.glb",
  "/assets/agents/Cube_Female_1.glb",
  "/assets/agents/Cube_Female_2.glb",
] as const;

export const OFFICE_AVATAR_POSE_BONES = {
  hips: "Hips",
  body: "Body",
  upperLegLeft: "UpperLegL",
  lowerLegLeft: "LowerLegL",
  upperLegRight: "UpperLegR",
  lowerLegRight: "LowerLegR",
  upperArmLeft: "UpperArmL",
  lowerArmLeft: "LowerArmL",
  upperArmRight: "UpperArmR",
  lowerArmRight: "LowerArmR",
  wristLeft: "WristL",
  wristRight: "WristR",
} as const;

function hashAgentId(id: string): number {
  let hash = 2_166_136_261;
  for (let index = 0; index < id.length; index += 1) {
    hash ^= id.charCodeAt(index);
    hash = Math.imul(hash, 16_777_619);
  }
  return hash >>> 0;
}

export function assignOfficeAvatarModels(agentIds: string[]): Map<string, string> {
  const orderedIds = [...new Set(agentIds)].sort((left, right) => left.localeCompare(right));
  const modelLoads = new Array<number>(OFFICE_AVATAR_MODELS.length).fill(0);
  const assignments = new Map<string, string>();

  for (const id of orderedIds) {
    const preferredIndex = hashAgentId(id) % OFFICE_AVATAR_MODELS.length;
    let modelIndex = preferredIndex;
    for (let offset = 0; offset < OFFICE_AVATAR_MODELS.length; offset += 1) {
      const candidate = (preferredIndex + offset) % OFFICE_AVATAR_MODELS.length;
      if (modelLoads[candidate] < modelLoads[modelIndex]!) modelIndex = candidate;
      if (modelLoads[candidate] === 0) {
        modelIndex = candidate;
        break;
      }
    }
    modelLoads[modelIndex] = modelLoads[modelIndex]! + 1;
    assignments.set(id, OFFICE_AVATAR_MODELS[modelIndex]!);
  }

  return assignments;
}
