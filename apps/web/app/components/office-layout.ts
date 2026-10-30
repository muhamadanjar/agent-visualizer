export type OfficePosition = [number, number, number];

export type OfficeStationLayout = {
  workstationPosition: OfficePosition;
  agentPosition: OfficePosition;
  chairPosition: OfficePosition;
  restPosition: OfficePosition;
  restSeatPosition: OfficePosition;
  routeToWork: OfficePosition[];
  routeToRest: OfficePosition[];
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

export function createOfficeLayout(agentCount: number): OfficeLayout {
  const count = Number.isFinite(agentCount) ? Math.max(0, Math.floor(agentCount)) : 0;
  const columns = Math.min(MAX_COLUMNS, Math.max(1, count));
  const rows = Math.max(1, Math.ceil(count / columns));
  const roomWidth = Math.max(16, columns * STATION_SPACING_X + 3.6);
  const restColumns = Math.min(4, Math.max(1, Math.ceil(Math.sqrt(count))));
  const restRows = Math.max(1, Math.ceil(count / restColumns));
  const restRoomDepth = Math.max(
    BASE_REST_ROOM_DEPTH,
    (restRows - 1) * REST_ROW_SPACING + REST_ROOM_EDGE_CLEARANCE,
  );
  const reservedRearDepth = SHARED_AREA_DEPTH + restRoomDepth;
  const roomDepth = Math.max(
    10,
    rows * STATION_SPACING_Z + ROOM_DEPTH_MARGIN + reservedRearDepth,
  );
  const restRoomRearZ = -roomDepth / 2 + ROOM_REAR_WALL_OFFSET;
  const restRoomFrontZ = restRoomRearZ + restRoomDepth;
  const restRoomCenterZ = restRoomRearZ + restRoomDepth / 2;
  const sharedAreaCenterZ = restRoomFrontZ + SHARED_AREA_DEPTH / 2;
  const workspaceOffsetZ = reservedRearDepth / 2;
  const restRowSpacing = Math.min(
    REST_ROW_SPACING,
    (restRoomDepth - REST_ROOM_EDGE_CLEARANCE) / Math.max(1, restRows - 1),
  );
  const restColumnSpacing = 2.6;
  const corridorX = roomWidth / 2 - 1;
  const restRoomDoorX = roomWidth / 2 - 1.45;
  const doorInside: OfficePosition = [restRoomDoorX, 0, restRoomFrontZ - 0.55];
  const doorOutside: OfficePosition = [restRoomDoorX, 0, restRoomFrontZ + 0.55];
  const restRoomFrontAisleZ = restRoomFrontZ - 0.55;
  const corridorAtRestDoor: OfficePosition = [corridorX, 0, restRoomFrontZ + 0.55];
  const stations: OfficeStationLayout[] = [];

  for (let index = 0; index < count; index += 1) {
    const row = Math.floor(index / columns);
    const column = index % columns;
    const rowLength = Math.min(columns, count - row * columns);
    const x = (column - (rowLength - 1) / 2) * STATION_SPACING_X;
    const z = (row - (rows - 1) / 2) * STATION_SPACING_Z + workspaceOffsetZ;
    const restRow = Math.floor(index / restColumns);
    const restColumn = index % restColumns;
    const restRowLength = Math.min(restColumns, count - restRow * restColumns);
    const restX = (restColumn - (restRowLength - 1) / 2) * restColumnSpacing;
    const restZ = restRoomCenterZ + (restRow - (restRows - 1) / 2) * restRowSpacing;
    const chairPosition: OfficePosition = [x, 0, z + CHAIR_OFFSET_Z];
    const restSeatPosition: OfficePosition = [restX, 0, restZ];
    const restPosition = restSeatPosition;
    const corridorAtWorkstation: OfficePosition = [corridorX, 0, chairPosition[2]];

    stations.push({
      workstationPosition: [x, 0, z],
      agentPosition: [x, 0, z + AGENT_OFFSET_Z],
      chairPosition,
      restPosition,
      restSeatPosition,
      routeToWork: [
        [restX, 0, restRoomFrontAisleZ],
        doorInside,
        doorOutside,
        corridorAtRestDoor,
        [corridorX, 0, chairPosition[2]],
        chairPosition,
      ],
      routeToRest: [
        corridorAtWorkstation,
        corridorAtRestDoor,
        doorOutside,
        doorInside,
        [restX, 0, restRoomFrontAisleZ],
        restPosition,
      ],
    });
  }

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
    stations,
  };
}
