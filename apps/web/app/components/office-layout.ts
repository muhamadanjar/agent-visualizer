export type OfficePosition = [number, number, number];

export const OFFICE_AREAS = [
  "architecture",
  "engineering",
  "review",
  "security",
  "meeting",
  "lounge",
] as const;
export type OfficeArea = (typeof OFFICE_AREAS)[number];

export const OFFICE_AREA_LABELS: Record<OfficeArea, string> = {
  architecture: "Architecture",
  engineering: "Engineering",
  review: "Review",
  security: "Security",
  meeting: "Meeting",
  lounge: "Lounge",
};

export type OfficeStationLayout = {
  officeArea: OfficeArea;
  workstationPosition: OfficePosition;
  agentPosition: OfficePosition;
  chairPosition: OfficePosition;
  restPosition: OfficePosition;
  restSeatPosition: OfficePosition;
  routeToWork: OfficePosition[];
  routeToRest: OfficePosition[];
};

export type OfficeAreaLayout = {
  officeArea: OfficeArea;
  center: OfficePosition;
  width: number;
  depth: number;
};

export type OfficeCommonAreaLayout = {
  center: OfficePosition;
  width: number;
  depth: number;
};

export type OfficeLayout = {
  columns: number;
  rows: number;
  roomWidth: number;
  roomDepth: number;
  sharedAreaCenter: OfficePosition;
  sharedAreaDepth: number;
  restRoomCenter: OfficePosition;
  restRoomDepth: number;
  restRoomFrontZ: number;
  restRoomDoorX: number;
  areas: OfficeAreaLayout[];
  receptionArea: OfficeCommonAreaLayout;
  stations: OfficeStationLayout[];
};

const MAX_COLUMNS = 4;
const STATION_SPACING_X = 4.5;
const STATION_SPACING_Z = 4.6;
const AGENT_OFFSET_Z = 1.02;
const CHAIR_OFFSET_Z = 1.2;
const SHARED_AREA_DEPTH = 4.6;
const BASE_REST_ROOM_DEPTH = 6.8;
const REST_ROW_SPACING = 2.6;
const REST_ROOM_EDGE_CLEARANCE = 2.4;
const ROOM_DEPTH_MARGIN = 4.2;
const ROOM_REAR_WALL_OFFSET = 0.12;
const AREA_COLUMNS = 2;
const AREA_WIDTH = 8.6;
const AREA_COLUMN_SPACING = 9.4;

function restRoomDimensions(count: number) {
  const restColumns = Math.min(4, Math.max(1, Math.ceil(Math.sqrt(count))));
  const restRows = Math.max(1, Math.ceil(count / restColumns));
  const restRoomDepth = Math.max(
    BASE_REST_ROOM_DEPTH,
    (restRows - 1) * REST_ROW_SPACING + REST_ROOM_EDGE_CLEARANCE,
  );
  return { restColumns, restRows, restRoomDepth };
}

function createOfficeEnvelope(
  count: number,
  columns: number,
  rows: number,
  roomWidth: number,
  stations: OfficeStationLayout[],
  areas: OfficeAreaLayout[],
): OfficeLayout {
  const { restColumns, restRows, restRoomDepth } = restRoomDimensions(count);
  const reservedRearDepth = SHARED_AREA_DEPTH + restRoomDepth;
  const roomDepth = Math.max(
    10,
    rows * STATION_SPACING_Z + ROOM_DEPTH_MARGIN + reservedRearDepth,
  );
  const restRoomRearZ = -roomDepth / 2 + ROOM_REAR_WALL_OFFSET;
  const restRoomFrontZ = restRoomRearZ + restRoomDepth;
  const restRoomCenterZ = restRoomRearZ + restRoomDepth / 2;
  const sharedAreaCenterZ = restRoomFrontZ + SHARED_AREA_DEPTH / 2;
  const receptionArea: OfficeCommonAreaLayout = {
    center: [roomWidth / 4, 0, sharedAreaCenterZ],
    width: roomWidth / 2 - 1,
    depth: SHARED_AREA_DEPTH - 0.4,
  };
  const restRowSpacing = Math.min(
    REST_ROW_SPACING,
    (restRoomDepth - REST_ROOM_EDGE_CLEARANCE) / Math.max(1, restRows - 1),
  );
  const corridorX = roomWidth / 2 - 1;
  const restRoomDoorX = roomWidth / 2 - 1.45;
  const doorInside: OfficePosition = [restRoomDoorX, 0, restRoomFrontZ - 0.55];
  const doorOutside: OfficePosition = [restRoomDoorX, 0, restRoomFrontZ + 0.55];
  const restRoomFrontAisleZ = restRoomFrontZ - 0.55;
  const positionedStations = stations.map((station, index) => {
    const restRow = Math.floor(index / restColumns);
    const restColumn = index % restColumns;
    const restRowLength = Math.min(restColumns, count - restRow * restColumns);
    const restX = (restColumn - (restRowLength - 1) / 2) * 2.6;
    const restZ = restRoomCenterZ + (restRow - (restRows - 1) / 2) * restRowSpacing;
    const restSeatPosition: OfficePosition = [restX, 0, restZ];
    const chairPosition = station.chairPosition;
    const corridorAtWorkstation: OfficePosition = [corridorX, 0, chairPosition[2]];
    return {
      ...station,
      restPosition: restSeatPosition,
      restSeatPosition,
      routeToWork: [
        [restX, 0, restRoomFrontAisleZ],
        doorInside,
        doorOutside,
        [corridorX, 0, restRoomFrontZ + 0.55],
        corridorAtWorkstation,
        chairPosition,
      ] as OfficePosition[],
      routeToRest: [
        corridorAtWorkstation,
        [corridorX, 0, restRoomFrontZ + 0.55],
        doorOutside,
        doorInside,
        [restX, 0, restRoomFrontAisleZ],
        restSeatPosition,
      ] as OfficePosition[],
    };
  });

  return {
    columns,
    rows,
    roomWidth,
    roomDepth,
    sharedAreaCenter: [0, 0, sharedAreaCenterZ],
    sharedAreaDepth: SHARED_AREA_DEPTH,
    restRoomCenter: [0, 0, restRoomCenterZ],
    restRoomDepth,
    restRoomFrontZ,
    restRoomDoorX,
    areas,
    receptionArea,
    stations: positionedStations,
  };
}

function createBalancedOfficeLayout(count: number): OfficeLayout {
  const columns = Math.min(MAX_COLUMNS, Math.max(1, count));
  const rows = Math.max(1, Math.ceil(count / columns));
  const roomWidth = Math.max(16, columns * STATION_SPACING_X + 3.6);
  const { restRoomDepth } = restRoomDimensions(count);
  const workspaceOffsetZ = (SHARED_AREA_DEPTH + restRoomDepth) / 2;
  const stations: OfficeStationLayout[] = [];

  for (let index = 0; index < count; index += 1) {
    const row = Math.floor(index / columns);
    const column = index % columns;
    const rowLength = Math.min(columns, count - row * columns);
    const x = (column - (rowLength - 1) / 2) * STATION_SPACING_X;
    const z = (row - (rows - 1) / 2) * STATION_SPACING_Z + workspaceOffsetZ;
    const chairPosition: OfficePosition = [x, 0, z + CHAIR_OFFSET_Z];
    stations.push({
      officeArea: OFFICE_AREAS[index % OFFICE_AREAS.length]!,
      workstationPosition: [x, 0, z],
      agentPosition: [x, 0, z + AGENT_OFFSET_Z],
      chairPosition,
      restPosition: [0, 0, 0],
      restSeatPosition: [0, 0, 0],
      routeToWork: [],
      routeToRest: [],
    });
  }

  return createOfficeEnvelope(count, columns, rows, roomWidth, stations, []);
}

function createAreaOfficeLayout(officeAreas: readonly OfficeArea[]): OfficeLayout {
  const counts = new Map<OfficeArea, number>(OFFICE_AREAS.map((area) => [area, 0]));
  for (const area of officeAreas) counts.set(area, counts.get(area)! + 1);
  const areaRows = new Map(
    OFFICE_AREAS.map((area) => [area, Math.max(1, Math.ceil(counts.get(area)! / AREA_COLUMNS))]),
  );
  const maxAreaRows = Math.max(...areaRows.values());
  const totalRows = maxAreaRows * 2;
  const roomWidth = AREA_COLUMN_SPACING * 2 + AREA_WIDTH + 4;
  const { restRoomDepth } = restRoomDimensions(officeAreas.length);
  const workspaceOffsetZ = (SHARED_AREA_DEPTH + restRoomDepth) / 2;
  const areaIndexes = new Map<OfficeArea, number>(OFFICE_AREAS.map((area) => [area, 0]));
  const stations = officeAreas.map((officeArea) => {
    const index = areaIndexes.get(officeArea)!;
    areaIndexes.set(officeArea, index + 1);
    const areaIndex = OFFICE_AREAS.indexOf(officeArea);
    const localRow = Math.floor(index / AREA_COLUMNS);
    const localColumn = index % AREA_COLUMNS;
    const rowLength = Math.min(AREA_COLUMNS, counts.get(officeArea)! - localRow * AREA_COLUMNS);
    const x = (areaIndex % 3 - 1) * AREA_COLUMN_SPACING
      + (localColumn - (rowLength - 1) / 2) * STATION_SPACING_X;
    const globalRow = Math.floor(areaIndex / 3) * maxAreaRows + localRow;
    const z = (globalRow - (totalRows - 1) / 2) * STATION_SPACING_Z + workspaceOffsetZ;
    const chairPosition: OfficePosition = [x, 0, z + CHAIR_OFFSET_Z];
    return {
      officeArea,
      workstationPosition: [x, 0, z] as OfficePosition,
      agentPosition: [x, 0, z + AGENT_OFFSET_Z] as OfficePosition,
      chairPosition,
      restPosition: [0, 0, 0] as OfficePosition,
      restSeatPosition: [0, 0, 0] as OfficePosition,
      routeToWork: [],
      routeToRest: [],
    };
  });

  const areas: OfficeAreaLayout[] = OFFICE_AREAS.map((officeArea, index) => {
    const areaRowsForZone = areaRows.get(officeArea)!;
    const topRow = Math.floor(index / 3) * maxAreaRows;
    const firstZ = (topRow - (totalRows - 1) / 2) * STATION_SPACING_Z + workspaceOffsetZ;
    const lastZ = (topRow + areaRowsForZone - 1 - (totalRows - 1) / 2)
      * STATION_SPACING_Z + workspaceOffsetZ;
    return {
      officeArea,
      center: [(index % 3 - 1) * AREA_COLUMN_SPACING, 0, (firstZ + lastZ) / 2],
      width: AREA_WIDTH,
      depth: Math.max(STATION_SPACING_Z, lastZ - firstZ + STATION_SPACING_Z),
    };
  });

  return createOfficeEnvelope(
    officeAreas.length,
    AREA_COLUMNS,
    totalRows,
    roomWidth,
    stations,
    areas,
  );
}

export function createOfficeLayout(agentCount: number | readonly OfficeArea[]): OfficeLayout {
  if (typeof agentCount !== "number") return createAreaOfficeLayout(agentCount);
  const count = Number.isFinite(agentCount) ? Math.max(0, Math.floor(agentCount)) : 0;
  return createBalancedOfficeLayout(count);
}
