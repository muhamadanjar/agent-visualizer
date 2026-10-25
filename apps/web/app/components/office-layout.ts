export type OfficeLayoutId = "sample-v1" | "sample-v2";
export type OfficePosition = [number, number];
export type OfficeFurniture = {
  id: string;
  title: string;
  x: number;
  z: number;
  halfX: number;
  halfZ: number;
  height: number;
  color: string;
  detail: string;
};
export type OfficeLayout = {
  id: OfficeLayoutId;
  label: string;
  furniture: readonly OfficeFurniture[];
};

export const OFFICE_GRID_MIN = -7.5;
export const OFFICE_GRID_COUNT = 16;
export const OFFICE_CELL_SIZE = 1;
export const OFFICE_CHARACTER_RADIUS = 0.32;
export const SAFE_OFFICE_SPAWN: OfficePosition = [-2, 4];

const sharedFurniture: readonly OfficeFurniture[] = [
  { id: "research-desk", title: "Research desk", x: -6.4, z: -2.8, halfX: 0.85, halfZ: 0.5, height: 0.82, color: "#9c7654", detail: "A local desk prop in the sample research room." },
  { id: "build-desk", title: "Build desk", x: 6.2, z: -2.7, halfX: 0.85, halfZ: 0.5, height: 0.82, color: "#987052", detail: "A local desk prop in the sample build room." },
  { id: "review-desk", title: "Review desk", x: 6.0, z: 2.8, halfX: 0.78, halfZ: 0.48, height: 0.82, color: "#9b7757", detail: "A local desk prop in the sample review room." },
  { id: "lounge-table", title: "Lounge table", x: 0, z: 5.0, halfX: 0.8, halfZ: 0.48, height: 0.52, color: "#a47a52", detail: "A local furniture prop in the sample lounge." },
];

export const OFFICE_LAYOUTS: Readonly<Record<OfficeLayoutId, OfficeLayout>> = {
  "sample-v1": {
    id: "sample-v1",
    label: "Sample layout · v1",
    furniture: [
      ...sharedFurniture,
      { id: "meeting-table", title: "Meeting table", x: 0, z: 0, halfX: 1.18, halfZ: 0.72, height: 0.72, color: "#66817a", detail: "A local meeting table. No real task or team is attached." },
    ],
  },
  "sample-v2": {
    id: "sample-v2",
    label: "Sample layout · v2",
    furniture: [
      ...sharedFurniture,
      { id: "meeting-table", title: "Meeting table", x: 0.8, z: 0.8, halfX: 1.18, halfZ: 0.72, height: 0.72, color: "#66817a", detail: "A local meeting table in the revised sample layout. No real task or team is attached." },
    ],
  },
};

export function isOfficeLayoutId(value: unknown): value is OfficeLayoutId {
  return value === "sample-v1" || value === "sample-v2";
}

export function worldToOfficeCell(x: number, z: number): { x: number; z: number } | null {
  const column = Math.round((x - OFFICE_GRID_MIN) / OFFICE_CELL_SIZE);
  const row = Math.round((z - OFFICE_GRID_MIN) / OFFICE_CELL_SIZE);
  if (column < 0 || column >= OFFICE_GRID_COUNT || row < 0 || row >= OFFICE_GRID_COUNT) return null;
  return { x: column, z: row };
}

export function isOfficeWorldPositionWalkable(x: number, z: number, layoutId: OfficeLayoutId): boolean {
  if (!Number.isFinite(x) || !Number.isFinite(z) || x < -7.8 || x > 7.8 || z < -5.5 || z > 5.5) return false;
  const column = Math.round((x - OFFICE_GRID_MIN) / OFFICE_CELL_SIZE);
  const row = Math.round((z - OFFICE_GRID_MIN) / OFFICE_CELL_SIZE);
  if (column < 0 || column >= OFFICE_GRID_COUNT || row < 0 || row >= OFFICE_GRID_COUNT) return false;
  const cellX = OFFICE_GRID_MIN + column * OFFICE_CELL_SIZE;
  const cellZ = OFFICE_GRID_MIN + row * OFFICE_CELL_SIZE;
  return !OFFICE_LAYOUTS[layoutId].furniture.some((item) =>
    (Math.abs(x - item.x) <= item.halfX + OFFICE_CHARACTER_RADIUS && Math.abs(z - item.z) <= item.halfZ + OFFICE_CHARACTER_RADIUS)
    || (Math.abs(cellX - item.x) <= item.halfX + OFFICE_CHARACTER_RADIUS && Math.abs(cellZ - item.z) <= item.halfZ + OFFICE_CHARACTER_RADIUS),
  );
}

export function isOfficePositionWalkable(position: OfficePosition, layoutId: OfficeLayoutId): boolean {
  return isOfficeWorldPositionWalkable(position[0], position[1], layoutId);
}
