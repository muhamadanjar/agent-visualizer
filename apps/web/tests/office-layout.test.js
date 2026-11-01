import { expect, test } from "bun:test";
import { createOfficeLayout } from "../app/components/office-layout.ts";
import { calculateOfficeCameraFitDistance } from "../app/components/office-camera.ts";
function positionKey([x, y, z]) {
  return `${x}:${y}:${z}`;
}

test("each persisted agent gets a collision-free workstation and dedicated rest seat", () => {
  const layout = createOfficeLayout(7);

  expect(layout.columns).toBe(4);
  expect(layout.rows).toBe(2);
  expect(layout.stations).toHaveLength(7);
  expect(new Set(layout.stations.map(({ workstationPosition }) => positionKey(workstationPosition))).size).toBe(7);
  expect(layout.roomWidth).toBeGreaterThan(20);
  expect(layout.roomDepth).toBeGreaterThan(12);
  expect(layout.sharedAreaDepth).toBeGreaterThan(4);
  expect(layout.stations[0].workstationPosition[2] - (layout.sharedAreaCenter[2] + layout.sharedAreaDepth / 2)).toBeGreaterThan(2);

  expect(layout.restRoomCenter[2] + layout.restRoomDepth / 2).toBeCloseTo(layout.restRoomFrontZ);
  expect(layout.sharedAreaCenter[2] - layout.sharedAreaDepth / 2).toBeCloseTo(layout.restRoomFrontZ);
  expect(new Set(layout.stations.map(({ restSeatPosition }) => positionKey(restSeatPosition))).size).toBe(7);
  expect(new Set(layout.stations.map(({ restPosition }) => positionKey(restPosition))).size).toBe(7);

  for (const {
    workstationPosition: desk,
    agentPosition: agent,
    chairPosition: chair,
    restPosition,
    restSeatPosition,
    routeToWork,
    routeToRest,
  } of layout.stations) {
    expect(agent[0]).toBe(desk[0]);
    expect(agent[2] - desk[2]).toBeCloseTo(1.02);
    expect(chair[0]).toBe(desk[0]);
    expect(chair[2] - desk[2]).toBeCloseTo(1.2);
    expect(restPosition[0]).toBe(restSeatPosition[0]);
    expect(restPosition).toEqual(restSeatPosition);
    expect(routeToWork[0][0]).toBe(restSeatPosition[0]);
    expect(routeToWork[0][2]).toBeGreaterThan(restSeatPosition[2]);
    expect(routeToRest[routeToRest.length - 2][0]).toBe(restSeatPosition[0]);
    expect(routeToWork[routeToWork.length - 1]).toEqual(chair);
    expect(routeToRest[routeToRest.length - 1]).toEqual(restPosition);
  }
});

test("resting avatars keep visible clearance across a growing roster", () => {
  const { stations } = createOfficeLayout(21);

  for (let first = 0; first < stations.length; first += 1) {
    for (let second = first + 1; second < stations.length; second += 1) {
      const [firstX, , firstZ] = stations[first].restPosition;
      const [secondX, , secondZ] = stations[second].restPosition;
      expect(Math.hypot(firstX - secondX, firstZ - secondZ)).toBeGreaterThanOrEqual(2.4);
    }
  }
});

test("the room grows with every agent rather than dropping overflow workstations", () => {
  const layout = createOfficeLayout(21);

  expect(layout.stations).toHaveLength(21);
  expect(layout.rows).toBe(6);
  expect(layout.roomDepth).toBeGreaterThan(createOfficeLayout(4).roomDepth);
  expect(layout.stations[0].workstationPosition[2] - (layout.sharedAreaCenter[2] + layout.sharedAreaDepth / 2)).toBeGreaterThan(2);
  expect(new Set(layout.stations.map(({ workstationPosition }) => positionKey(workstationPosition))).size).toBe(21);
  expect(layout.restRoomDepth).toBeGreaterThan(createOfficeLayout(4).restRoomDepth);
  expect(new Set(layout.stations.map(({ restSeatPosition }) => positionKey(restSeatPosition))).size).toBe(21);
});

test("assigned office areas create distinct named zones without losing stations", () => {
  const officeAreas = [
    "architecture", "engineering", "review", "security", "meeting", "lounge",
    "architecture", "engineering", "review", "security", "meeting", "lounge",
    "architecture", "engineering", "review", "security", "meeting", "lounge",
    "architecture", "engineering", "review",
  ];
  const layout = createOfficeLayout(officeAreas);

  expect(layout.stations).toHaveLength(officeAreas.length);
  expect(layout.areas).toHaveLength(6);
  expect(layout.stations.map(({ officeArea }) => officeArea)).toEqual(officeAreas);
  expect(new Set(layout.stations.map(({ workstationPosition }) => positionKey(workstationPosition))).size)
    .toBe(officeAreas.length);
  for (const station of layout.stations) {
    const zone = layout.areas.find((area) => area.officeArea === station.officeArea);
    expect(zone).toBeDefined();
    expect(Math.abs(station.workstationPosition[0] - zone.center[0])).toBeLessThan(zone.width / 2);
    expect(Math.abs(station.workstationPosition[2] - zone.center[2])).toBeLessThan(zone.depth / 2);
  }
});

test("Reception furniture stays in a separate shared room within the elastic envelope", () => {
  const areas = ["architecture", "engineering", "review", "security", "meeting", "lounge"];
  for (const count of [0, 1, 7, 21, 60]) {
    const layouts = [
      createOfficeLayout(count),
      createOfficeLayout(Array.from({ length: count }, (_, index) => areas[index % areas.length])),
    ];
    for (const layout of layouts) {
      const reception = layout.receptionArea;
      const receptionLeft = reception.center[0] - reception.width / 2;
      const receptionRight = reception.center[0] + reception.width / 2;
      const receptionFront = reception.center[2] - reception.depth / 2;
      const receptionBack = reception.center[2] + reception.depth / 2;
      const meetingBoardRight = -layout.roomWidth * 0.24 + 1.31;

      expect("officeArea" in reception).toBe(false);
      expect(reception.center[2]).toBe(layout.sharedAreaCenter[2]);
      expect(reception.depth).toBeLessThan(layout.sharedAreaDepth);
      expect(reception.width).toBeGreaterThanOrEqual(3.55);
      expect(reception.depth).toBeGreaterThanOrEqual(3.22);
      expect(receptionLeft).toBeGreaterThan(meetingBoardRight);
      expect(receptionRight).toBeLessThan(layout.roomWidth / 2 - 0.2);
      expect(reception.center[0] - 3.55 / 2).toBeGreaterThan(receptionLeft);
      expect(reception.center[0] + 3.55 / 2).toBeLessThan(receptionRight);
      expect(reception.center[2] - 1.42).toBeGreaterThan(receptionFront);
      expect(reception.center[2] + 1.61).toBeLessThan(receptionBack);
      expect(receptionBack).toBeLessThan(layout.roomDepth / 2);
    }
  }
});

test("an empty office stays empty while retaining a usable room envelope", () => {
  const layout = createOfficeLayout(0);

  expect(layout.stations).toHaveLength(0);
  expect(layout.roomWidth).toBeGreaterThan(0);
  expect(layout.roomDepth).toBeGreaterThan(0);
  expect(layout.sharedAreaCenter[2] - layout.sharedAreaDepth / 2).toBeGreaterThan(-layout.roomDepth / 2);
  expect(layout.sharedAreaCenter[2] + layout.sharedAreaDepth / 2).toBeLessThan(layout.roomDepth / 2);
});

test("portrait camera framing fits the diagonal office footprint and shared-room walls", () => {
  const mobileDistance = calculateOfficeCameraFitDistance(32.8, 34, 362 / 490);
  const desktopDistance = calculateOfficeCameraFitDistance(32.8, 34, 805 / 558);

  expect(mobileDistance).toBeGreaterThanOrEqual(98);
  expect(desktopDistance).toBeGreaterThanOrEqual(60);
});
