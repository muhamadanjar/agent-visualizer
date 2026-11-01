export const OFFICE_CAMERA_FOV = 42;
export const OFFICE_CAMERA_OFFSET = [0.48, 0.86, 0.62] as const;
export const OFFICE_CAMERA_OFFSET_LENGTH = Math.hypot(...OFFICE_CAMERA_OFFSET);

const OFFICE_ROOM_WALL_HEIGHT = 4.2;
const OFFICE_CAMERA_TARGET_HEIGHT = 0.8;
const OFFICE_CAMERA_FRAME_MARGIN = 1.2;

export function calculateOfficeCameraFitDistance(roomWidth: number, roomDepth: number, aspect: number): number {
  const verticalHalfFov = (OFFICE_CAMERA_FOV * Math.PI) / 360;
  const horizontalHalfFov = Math.atan(Math.tan(verticalHalfFov) * Math.max(0.1, aspect));
  const [offsetX, offsetY, offsetZ] = OFFICE_CAMERA_OFFSET;
  const horizontalLength = Math.hypot(offsetX, offsetZ);
  const rightX = Math.abs(offsetZ) / horizontalLength;
  const rightZ = Math.abs(offsetX) / horizontalLength;
  const forwardX = Math.abs(offsetX) / horizontalLength;
  const forwardZ = Math.abs(offsetZ) / horizontalLength;
  const horizontalHalfExtent = (roomWidth * rightX + roomDepth * rightZ) / 2;
  const forwardHalfExtent = (roomWidth * forwardX + roomDepth * forwardZ) / 2;
  const sinElevation = Math.abs(offsetY) / OFFICE_CAMERA_OFFSET_LENGTH;
  const cosElevation = horizontalLength / OFFICE_CAMERA_OFFSET_LENGTH;
  const verticalHalfExtent = forwardHalfExtent * sinElevation
    + Math.max(OFFICE_CAMERA_TARGET_HEIGHT, OFFICE_ROOM_WALL_HEIGHT - OFFICE_CAMERA_TARGET_HEIGHT) * cosElevation;

  return Math.max(
    horizontalHalfExtent / Math.tan(horizontalHalfFov),
    verticalHalfExtent / Math.tan(verticalHalfFov),
  ) * OFFICE_CAMERA_FRAME_MARGIN;
}
