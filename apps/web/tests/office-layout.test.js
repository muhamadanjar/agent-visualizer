import { expect, test } from "bun:test";
import { createOfficeLayout } from "../app/components/office-layout.ts";

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

test("an empty office stays empty while retaining a usable room envelope", () => {
  const layout = createOfficeLayout(0);

  expect(layout.stations).toHaveLength(0);
  expect(layout.roomWidth).toBeGreaterThan(0);
  expect(layout.roomDepth).toBeGreaterThan(0);
  expect(layout.sharedAreaCenter[2] - layout.sharedAreaDepth / 2).toBeGreaterThan(-layout.roomDepth / 2);
  expect(layout.sharedAreaCenter[2] + layout.sharedAreaDepth / 2).toBeLessThan(layout.roomDepth / 2);
});
