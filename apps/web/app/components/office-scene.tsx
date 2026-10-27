"use client";

import { Html, OrbitControls } from "@react-three/drei";
import { Canvas, useFrame, useThree } from "@react-three/fiber";
import { CapsuleCollider, CuboidCollider, Physics, RigidBody, useRapier } from "@react-three/rapier";
import type { RapierRigidBody } from "@react-three/rapier";
import type { KinematicCharacterController } from "@dimforge/rapier3d-compat";
import { useCallback, useEffect, useMemo, useRef } from "react";
import type { RefObject } from "react";
import { Vector3 } from "three";
import type { Group } from "three";
import {
  OFFICE_GRID_COUNT,
  OFFICE_GRID_MIN,
  OFFICE_CELL_SIZE,
  OFFICE_LAYOUTS,
  isOfficePositionWalkable,
  worldToOfficeCell,
  isOfficeWorldPositionWalkable,
  type OfficeFurniture,
  type OfficeLayoutId,
  type OfficePosition,
} from "./office-layout";

type CameraMode = "follow" | "overview";
type OfficeSceneProps = {
  cameraMode: CameraMode;
  zoom: number;
  focusTarget: [number, number, number];
  reducedMotion: boolean;
  layoutId: OfficeLayoutId;
  spawnPosition: OfficePosition;
  activeDestination: OfficePosition | null;
  onSelectObject: (title: string, detail: string) => void;
  onPlayerPosition: (x: number, z: number) => void;
  onMovementMessage: (message: string) => void;
  onDestinationChange: (position: OfficePosition | null) => void;
};
type Cell = { x: number; z: number };
type MoveTo = (x: number, z: number) => void;

const MOVE_SPEED = 3.15;
const NEIGHBORS = [
  { x: 1, z: 0, cost: 1 }, { x: -1, z: 0, cost: 1 },
  { x: 0, z: 1, cost: 1 }, { x: 0, z: -1, cost: 1 },
  { x: 1, z: 1, cost: Math.SQRT2 }, { x: 1, z: -1, cost: Math.SQRT2 },
  { x: -1, z: 1, cost: Math.SQRT2 }, { x: -1, z: -1, cost: Math.SQRT2 },
] as const;

function cellKey(cell: Cell): string { return `${cell.x},${cell.z}`; }
function cellToWorld(cell: Cell): Vector3 { return new Vector3(OFFICE_GRID_MIN + cell.x * OFFICE_CELL_SIZE, 0, OFFICE_GRID_MIN + cell.z * OFFICE_CELL_SIZE); }
function isBlocked(cell: Cell, layoutId: OfficeLayoutId): boolean {
  const x = OFFICE_GRID_MIN + cell.x * OFFICE_CELL_SIZE;
  const z = OFFICE_GRID_MIN + cell.z * OFFICE_CELL_SIZE;
  return !isOfficeWorldPositionWalkable(x, z, layoutId);
}
function heuristic(a: Cell, b: Cell): number {
  const dx = Math.abs(a.x - b.x);
  const dz = Math.abs(a.z - b.z);
  return Math.max(dx, dz) + (Math.SQRT2 - 1) * Math.min(dx, dz);
}
function findPath(start: Cell, goal: Cell, layoutId: OfficeLayoutId): Cell[] | null {
  if (isBlocked(start, layoutId) || isBlocked(goal, layoutId)) return null;
  const open = [start];
  const cameFrom = new Map<string, Cell>();
  const costs = new Map<string, number>([[cellKey(start), 0]]);
  const scores = new Map<string, number>([[cellKey(start), heuristic(start, goal)]]);
  const closed = new Set<string>();
  while (open.length) {
    open.sort((left, right) => (scores.get(cellKey(left)) ?? Infinity) - (scores.get(cellKey(right)) ?? Infinity));
    const current = open.shift();
    if (!current) return null;
    const currentKey = cellKey(current);
    if (current.x === goal.x && current.z === goal.z) {
      const path: Cell[] = [];
      let cursor = current;
      while (cellKey(cursor) !== cellKey(start)) {
        path.push(cursor);
        const parent = cameFrom.get(cellKey(cursor));
        if (!parent) return null;
        cursor = parent;
      }
      return path.reverse();
    }
    closed.add(currentKey);
    for (const direction of NEIGHBORS) {
      const next = { x: current.x + direction.x, z: current.z + direction.z };
      const nextKey = cellKey(next);
      if (next.x < 0 || next.x >= OFFICE_GRID_COUNT || next.z < 0 || next.z >= OFFICE_GRID_COUNT || isBlocked(next, layoutId) || closed.has(nextKey)) continue;
      if (direction.x !== 0 && direction.z !== 0 && (isBlocked({ x: current.x + direction.x, z: current.z }, layoutId) || isBlocked({ x: current.x, z: current.z + direction.z }, layoutId))) continue;
      const tentative = (costs.get(currentKey) ?? Infinity) + direction.cost;
      if (tentative >= (costs.get(nextKey) ?? Infinity)) continue;
      cameFrom.set(nextKey, current);
      costs.set(nextKey, tentative);
      scores.set(nextKey, tentative + heuristic(next, goal));
      if (!open.some((cell) => cellKey(cell) === nextKey)) open.push(next);
    }
  }
  return null;
}


function ClickableFurniture({ item, onSelect }: { item: OfficeFurniture; onSelect: (title: string, detail: string) => void }) {
  return (
    <RigidBody type="fixed" colliders={false} position={[item.x, 0, item.z]}>
      <CuboidCollider args={[item.halfX, item.height / 2, item.halfZ]} position={[0, item.height / 2, 0]} />
      <mesh position={[0, item.height / 2, 0]} castShadow receiveShadow onClick={(event) => { event.stopPropagation(); onSelect(item.title, item.detail); }}>
        <boxGeometry args={[item.halfX * 2, item.height, item.halfZ * 2]} /><meshLambertMaterial color={item.color} />
      </mesh>
      <mesh position={[0, item.height + 0.035, 0]} onClick={(event) => { event.stopPropagation(); onSelect(item.title, item.detail); }}>
        <boxGeometry args={[item.halfX * 2 + 0.12, 0.06, item.halfZ * 2 + 0.12]} /><meshLambertMaterial color="#d6ba8a" />
      </mesh>
      {([[-1, -1], [1, -1], [-1, 1], [1, 1]] as const).map(([x, z]) => <mesh key={`${x}-${z}`} position={[x * (item.halfX - 0.12), item.height / 2 - 0.02, z * (item.halfZ - 0.1)]}><boxGeometry args={[0.09, item.height - 0.08, 0.09]} /><meshLambertMaterial color="#70543c" /></mesh>)}
    </RigidBody>
  );
}

function WorkspaceTaskBoard({ onSelect }: { onSelect: (title: string, detail: string) => void }) {
  const open = (event: { stopPropagation: () => void }) => {
    event.stopPropagation();
    onSelect("Workspace task board", "A fixed office-template object. Open persisted Tasks to inspect real Tasks; no task data is rendered in the scene.");
  };
  return (
    <group position={[0, 0, -5.98]} onClick={open}>
      <mesh position={[0, 1.45, 0]} castShadow><boxGeometry args={[1.8, 1.05, 0.12]} /><meshLambertMaterial color="#775c43" /></mesh>
      <mesh position={[0, 1.45, 0.075]}><boxGeometry args={[1.58, 0.82, 0.03]} /><meshLambertMaterial color="#d7c9a9" /></mesh>
      <Html position={[0, 2.1, 0.08]} center distanceFactor={12} style={{ pointerEvents: "none" }}><span className="scene-room-label">WORKSPACE TASKS</span></Html>
    </group>
  );
}

function Doorway({ onSelect }: { onSelect: (title: string, detail: string) => void }) {
  const open = (event: { stopPropagation: () => void }) => {
    event.stopPropagation();
    onSelect("Office entrance", "A visual doorway marker; workspace permissions are not determined by room or proximity.");
  };
  return (
    <group position={[0, 0, 6.02]} onClick={open}>
      <mesh position={[-1.03, 1, 0]}><boxGeometry args={[0.12, 2, 0.16]} /><meshLambertMaterial color="#9e8c73" /></mesh>
      <mesh position={[1.03, 1, 0]}><boxGeometry args={[0.12, 2, 0.16]} /><meshLambertMaterial color="#9e8c73" /></mesh>
      <mesh position={[0, 1.95, 0]}><boxGeometry args={[2.18, 0.12, 0.16]} /><meshLambertMaterial color="#9e8c73" /></mesh>
    </group>
  );
}

function Plant({ position }: { position: [number, number, number] }) {
  return <group position={position}><mesh position={[0, 0.28, 0]}><cylinderGeometry args={[0.2, 0.24, 0.55, 7]} /><meshLambertMaterial color="#ba8058" /></mesh><mesh position={[0, 0.86, 0]}><coneGeometry args={[0.52, 1.15, 6]} /><meshLambertMaterial color="#58775c" /></mesh><mesh position={[0.56, 0.72, 0.1]} rotation={[0, 0, -0.35]}><coneGeometry args={[0.32, 0.75, 5]} /><meshLambertMaterial color="#78936a" /></mesh></group>;
}

function RoomLabel({ position, children }: { position: [number, number, number]; children: string }) {
  return <Html position={position} center distanceFactor={12} style={{ pointerEvents: "none" }}><span className="scene-room-label">{children}</span></Html>;
}


function LocalAvatar({ bodyRef, onSelect, reducedMotion, spawnPosition }: {
  bodyRef: RefObject<RapierRigidBody | null>;
  onSelect: () => void;
  reducedMotion: boolean;
  spawnPosition: OfficePosition;
}) {
  const avatarRef = useRef<Group>(null);
  const bobRef = useRef(0);
  useEffect(() => {
    bodyRef.current?.setTranslation({ x: spawnPosition[0], y: 0.72, z: spawnPosition[1] }, true);
  }, [bodyRef, spawnPosition]);
  useFrame((_, delta) => {
    const body = bodyRef.current;
    const avatar = avatarRef.current;
    if (!body || !avatar) return;
    const velocity = body.linvel();
    const moving = Math.abs(velocity.x) + Math.abs(velocity.z) > 0.05;
    bobRef.current += delta * (moving ? 8 : 2);
    avatar.position.y = -0.72 + (!reducedMotion && moving ? Math.sin(bobRef.current) * 0.025 : 0);
  });
  return (
    <RigidBody ref={bodyRef} type="kinematicPosition" colliders={false} position={[spawnPosition[0], 0.72, spawnPosition[1]]} enabledRotations={[false, false, false]} enabledTranslations={[true, false, true]}>
      <CapsuleCollider args={[0.35, 0.28]} />
      <group ref={avatarRef} position={[0, -0.72, 0]} onClick={(event) => { event.stopPropagation(); onSelect(); }}>
        <mesh position={[0, 0.52, 0]} castShadow><capsuleGeometry args={[0.29, 0.52, 5, 8]} /><meshLambertMaterial color="#42715d" /></mesh>
        <mesh position={[0, 1.02, 0]} castShadow><dodecahedronGeometry args={[0.26, 0]} /><meshLambertMaterial color="#d09b75" flatShading /></mesh>
        <mesh position={[0, 1.18, 0]}><sphereGeometry args={[0.24, 8, 5, 0, Math.PI * 2, 0, Math.PI * 0.55]} /><meshLambertMaterial color="#3c3833" flatShading /></mesh>
        <mesh position={[0.1, 1.03, 0.23]}><sphereGeometry args={[0.026, 6, 4]} /><meshBasicMaterial color="#2c2723" /></mesh>
        <Html position={[0, 1.56, 0]} center distanceFactor={13} style={{ pointerEvents: "none" }}><span className="scene-avatar-label you-label"><strong>You</strong><small>Local owner avatar</small></span></Html>
      </group>
    </RigidBody>
  );
}
function PlayerController({ bodyRef, moveToRef, onMovementMessage, onPlayerPosition, onSelectObject, layoutId, spawnPosition, activeDestination, onDestinationChange }: {
  bodyRef: RefObject<RapierRigidBody | null>;
  moveToRef: RefObject<MoveTo>;
  onMovementMessage: (message: string) => void;
  onPlayerPosition: (x: number, z: number) => void;
  onSelectObject: (title: string, detail: string) => void;
  layoutId: OfficeLayoutId;
  spawnPosition: OfficePosition;
  activeDestination: OfficePosition | null;
  onDestinationChange: (position: OfficePosition | null) => void;
}) {
  const { world } = useRapier();
  const controllerRef = useRef<KinematicCharacterController | null>(null);
  const keysRef = useRef(new Set<string>());
  const routeRef = useRef<Vector3[]>([]);
  const desiredRef = useRef(new Vector3());
  const nextTranslationRef = useRef({ x: 0, y: 0, z: 0 });
  const lastPositionReportRef = useRef(0);
  const lastBlockedReportRef = useRef(0);
  const objects = useMemo(() => [
    ...OFFICE_LAYOUTS[layoutId].furniture.map((item) => ({ title: item.title, x: item.x, z: item.z, detail: item.detail })),
    { title: "Workspace task board", x: 0, z: -5.98, detail: "A fixed office-template object. Open persisted Tasks to inspect real Tasks; no task data is rendered in the scene." },
    { title: "Office entrance", x: 0, z: 6.02, detail: "A visual doorway marker; workspace permissions are not determined by room or proximity." },
  ], [layoutId]);

  useEffect(() => {
    const controller = world.createCharacterController(0.02);
    controller.setUp({ x: 0, y: 1, z: 0 });
    controller.setSlideEnabled(true);
    controllerRef.current = controller;
    return () => { world.removeCharacterController(controller); controllerRef.current = null; };
  }, [world]);

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      const target = event.target;
      if (target instanceof HTMLElement && (target.isContentEditable || /^(INPUT|TEXTAREA|BUTTON|SELECT|A)$/.test(target.tagName))) return;
      const key = event.key.toLowerCase();
      if (["w", "a", "s", "d", "arrowup", "arrowdown", "arrowleft", "arrowright"].includes(key)) {
        event.preventDefault();
        keysRef.current.add(key);
        routeRef.current = [];
        onDestinationChange(null);
        onMovementMessage("Keyboard movement active. A click destination is canceled while steering manually.");
      }
      if (key === "e") {
        const position = bodyRef.current?.translation();
        if (!position) return;
        const nearest = objects.reduce<{ distance: number; item: (typeof objects)[number] } | null>((best, item) => {
          const distance = Math.hypot(position.x - item.x, position.z - item.z);
          return distance < 1.7 && (!best || distance < best.distance) ? { distance, item } : best;
        }, null);
        if (nearest) onSelectObject(nearest.item.title, nearest.item.detail);
        else onMovementMessage("No office-template object is within interaction range.");
      }
    };
    const onKeyUp = (event: KeyboardEvent) => keysRef.current.delete(event.key.toLowerCase());
    const onBlur = () => keysRef.current.clear();
    window.addEventListener("keydown", onKeyDown);
    window.addEventListener("keyup", onKeyUp);
    window.addEventListener("blur", onBlur);
    return () => { window.removeEventListener("keydown", onKeyDown); window.removeEventListener("keyup", onKeyUp); window.removeEventListener("blur", onBlur); };
  }, [bodyRef, objects, onDestinationChange, onMovementMessage, onSelectObject]);

  const moveTo = useCallback((x: number, z: number) => {
    const position = bodyRef.current?.translation();
    const start = position ? worldToOfficeCell(position.x, position.z) : null;
    const goal = worldToOfficeCell(x, z);
    if (!start || !goal || !isOfficePositionWalkable([x, z], layoutId)) {
      routeRef.current = [];
      onDestinationChange(null);
      onMovementMessage("Invalid destination: outside the walkable floor.");
      return;
    }
    const path = findPath(start, goal, layoutId);
    if (!path) {
      routeRef.current = [];
      onDestinationChange(null);
      onMovementMessage("Destination is blocked or unreachable. Choose a clear floor tile.");
      return;
    }
    routeRef.current = path.map(cellToWorld);
    onDestinationChange(path.length ? [x, z] : null);
    onMovementMessage(path.length ? `Local route set · ${path.length} steps.` : "Your local avatar is already at this point.");
  }, [bodyRef, layoutId, onDestinationChange, onMovementMessage]);
  moveToRef.current = moveTo;

  useEffect(() => {
    if (!activeDestination) return;
    const frame = requestAnimationFrame(() => moveToRef.current(activeDestination[0], activeDestination[1]));
    return () => cancelAnimationFrame(frame);
  }, [activeDestination, layoutId, moveToRef, spawnPosition]);

  useFrame((_, delta) => {
    const body = bodyRef.current;
    const controller = controllerRef.current;
    if (!body || !controller) return;
    const position = body.translation();
    if (performance.now() - lastPositionReportRef.current > 150) {
      lastPositionReportRef.current = performance.now();
      onPlayerPosition(position.x, position.z);
    }
    const keys = keysRef.current;
    let dx = Number(keys.has("d") || keys.has("arrowright")) - Number(keys.has("a") || keys.has("arrowleft"));
    let dz = Number(keys.has("s") || keys.has("arrowdown")) - Number(keys.has("w") || keys.has("arrowup"));
    const manual = dx !== 0 || dz !== 0;
    const followingRoute = !manual && routeRef.current.length > 0;
    if (!manual && routeRef.current.length) {
      let waypoint = routeRef.current[0];
      while (waypoint && Math.hypot(waypoint.x - position.x, waypoint.z - position.z) < 0.16) {
        routeRef.current.shift();
        waypoint = routeRef.current[0];
      }
      if (waypoint) { dx = waypoint.x - position.x; dz = waypoint.z - position.z; }
    }
    if (followingRoute && routeRef.current.length === 0) {
      onDestinationChange(null);
      onMovementMessage("Local route complete.");
      return;
    }
    const length = Math.hypot(dx, dz);
    if (!length) return;
    dx /= length;
    dz /= length;
    const step = Math.min(delta, 0.05) * MOVE_SPEED;
    const desired = desiredRef.current.set(dx * step, 0, dz * step);
    controller.computeColliderMovement(body.collider(0), desired);
    const actual = controller.computedMovement();
    if (actual.x === 0 && actual.z === 0) {
      if (routeRef.current.length && performance.now() - lastBlockedReportRef.current > 800) {
        lastBlockedReportRef.current = performance.now();
        routeRef.current = [];
        onDestinationChange(null);
        onMovementMessage("Movement stopped at a solid collider. The local avatar cannot pass through furniture or walls.");
      }
      return;
    }
    const next = nextTranslationRef.current;
    next.x = position.x + actual.x;
    next.y = position.y + actual.y;
    next.z = position.z + actual.z;
    body.setNextKinematicTranslation(next);
  });

  return null;
}


function CameraRig({ bodyRef, mode, zoom, focusTarget, reducedMotion }: {
  bodyRef: RefObject<RapierRigidBody | null>;
  mode: CameraMode;
  zoom: number;
  focusTarget: [number, number, number];
  reducedMotion: boolean;
}) {
  const { camera } = useThree();
  const desired = useMemo(() => new Vector3(), []);
  const target = useMemo(() => new Vector3(), []);
  useEffect(() => {
    if (mode !== "overview") return;
    target.set(focusTarget[0], focusTarget[1], focusTarget[2]);
    camera.position.set(target.x + zoom * 0.47, zoom * 0.92, target.z + zoom * 0.47);
    camera.up.set(0, 1, 0);
    camera.lookAt(target);
    camera.updateProjectionMatrix();
  }, [camera, focusTarget, mode, target, zoom]);
  useFrame((_, delta) => {
    if (mode !== "follow") return;
    const position = bodyRef.current?.translation();
    if (!position) return;
    target.set(position.x, 0, position.z);
    desired.set(position.x + zoom * 0.38, position.y + zoom * 0.65, position.z + zoom * 0.45);
    if (reducedMotion) camera.position.copy(desired);
    else camera.position.lerp(desired, 1 - Math.exp(-delta * 4.5));
    camera.up.set(0, 1, 0);
    camera.lookAt(target);
  });
  return null;
}

function RoomFloors() {
  return (
    <>
      <mesh position={[-4.4, 0.015, -2.4]} rotation={[-Math.PI / 2, 0, 0]}><planeGeometry args={[6, 5.5]} /><meshLambertMaterial color="#c4d0bd" /></mesh>
      <mesh position={[4.3, 0.016, -2.4]} rotation={[-Math.PI / 2, 0, 0]}><planeGeometry args={[6.5, 5.5]} /><meshLambertMaterial color="#d6c5a9" /></mesh>
      <mesh position={[4.3, 0.017, 3.4]} rotation={[-Math.PI / 2, 0, 0]}><planeGeometry args={[6.5, 5.4]} /><meshLambertMaterial color="#c2ced0" /></mesh>
      <mesh position={[-4.3, 0.018, 3.4]} rotation={[-Math.PI / 2, 0, 0]}><planeGeometry args={[6.3, 5.4]} /><meshLambertMaterial color="#ddc9ad" /></mesh>
    </>
  );
}

function OfficeStructure() {
  return (
    <RigidBody type="fixed" colliders={false}>
      <CuboidCollider position={[0, -0.14, 0]} args={[8.45, 0.14, 6.35]} />
      <CuboidCollider position={[0, 0.65, -6.25]} args={[8.45, 0.65, 0.15]} />
      <CuboidCollider position={[-8.35, 0.65, 0]} args={[0.15, 0.65, 6.35]} />
      <CuboidCollider position={[8.35, 0.65, 0]} args={[0.15, 0.65, 6.35]} />
      <CuboidCollider position={[-4.8, 0.65, 6.2]} args={[3.55, 0.65, 0.15]} />
      <CuboidCollider position={[4.8, 0.65, 6.2]} args={[3.55, 0.65, 0.15]} />
      <mesh position={[0, -0.16, 0]} receiveShadow><boxGeometry args={[17, 0.3, 12.7]} /><meshLambertMaterial color="#e7dfd0" /></mesh>
      <mesh position={[0, 0.65, -6.25]} receiveShadow><boxGeometry args={[16.9, 1.3, 0.3]} /><meshLambertMaterial color="#c7bba6" /></mesh>
      <mesh position={[-8.35, 0.65, 0]} receiveShadow><boxGeometry args={[0.3, 1.3, 12.7]} /><meshLambertMaterial color="#c7bba6" /></mesh>
      <mesh position={[8.35, 0.65, 0]} receiveShadow><boxGeometry args={[0.3, 1.3, 12.7]} /><meshLambertMaterial color="#c7bba6" /></mesh>
      <mesh position={[-4.8, 0.65, 6.2]}><boxGeometry args={[7.1, 1.3, 0.3]} /><meshLambertMaterial color="#c7bba6" /></mesh>
      <mesh position={[4.8, 0.65, 6.2]}><boxGeometry args={[7.1, 1.3, 0.3]} /><meshLambertMaterial color="#c7bba6" /></mesh>
    </RigidBody>
  );
}

function SceneContents({ props }: { props: OfficeSceneProps }) {
  const bodyRef = useRef<RapierRigidBody>(null);
  const moveToRef = useRef<MoveTo>(() => {});
  const onLocalAvatarSelect = useCallback(() => props.onSelectObject("Your local avatar", "Presentation-only movement; not an Agent Instance or runtime session."), [props]);

  return (
    <>
      <color attach="background" args={["#eee8dc"]} />
      <ambientLight intensity={1.5} />
      <directionalLight position={[-6, 11, 4]} intensity={2.2} castShadow shadow-mapSize-width={1024} shadow-mapSize-height={1024} />
      <hemisphereLight args={["#fffaf0", "#8d927f", 0.65]} />
      <Physics gravity={[0, -9.81, 0]} colliders={false} timeStep="vary">
        <OfficeStructure />
        <RoomFloors />
        {OFFICE_LAYOUTS[props.layoutId].furniture.map((item) => <ClickableFurniture key={item.id} item={item} onSelect={props.onSelectObject} />)}
        <WorkspaceTaskBoard onSelect={props.onSelectObject} />
        <Doorway onSelect={props.onSelectObject} />
        <Plant position={[-7.2, 0, -5.1]} /><Plant position={[7.1, 0, 4.9]} /><Plant position={[-7.2, 0, 5.0]} />
        <mesh position={[-4.3, 0.03, -5.15]} rotation={[-Math.PI / 2, 0, 0]}><cylinderGeometry args={[0.42, 0.42, 0.02, 12]} /><meshLambertMaterial color="#a8b99a" /></mesh>
        <mesh position={[4.2, 0.03, -5.15]} rotation={[-Math.PI / 2, 0, 0]}><cylinderGeometry args={[0.42, 0.42, 0.02, 12]} /><meshLambertMaterial color="#c9a67b" /></mesh>
        <mesh position={[4.2, 0.03, 5.0]} rotation={[-Math.PI / 2, 0, 0]}><cylinderGeometry args={[0.42, 0.42, 0.02, 12]} /><meshLambertMaterial color="#a6bdc3" /></mesh>
        <RoomLabel position={[-4.7, 0.15, -5.0]}>RESEARCH</RoomLabel>
        <RoomLabel position={[4.3, 0.15, -5.0]}>BUILD ROOM</RoomLabel>
        <RoomLabel position={[4.3, 0.15, 5.1]}>REVIEW</RoomLabel>
        <RoomLabel position={[-4.5, 0.15, 5.1]}>SHARED STUDIO</RoomLabel>
        <mesh position={[0, 0.035, 0]} rotation={[-Math.PI / 2, 0, 0]} onClick={(event) => { event.stopPropagation(); moveToRef.current(event.point.x, event.point.z); }}>
          <planeGeometry args={[16.6, 12]} /><meshBasicMaterial transparent opacity={0} depthWrite={false} />
        </mesh>
        
        <LocalAvatar bodyRef={bodyRef} onSelect={onLocalAvatarSelect} reducedMotion={props.reducedMotion} spawnPosition={props.spawnPosition} />
        <PlayerController
          bodyRef={bodyRef}
          moveToRef={moveToRef}
          onMovementMessage={props.onMovementMessage}
          onPlayerPosition={props.onPlayerPosition}
          onSelectObject={props.onSelectObject}
          layoutId={props.layoutId}
          spawnPosition={props.spawnPosition}
          activeDestination={props.activeDestination}
          onDestinationChange={props.onDestinationChange}
        />
      </Physics>
      <CameraRig bodyRef={bodyRef} mode={props.cameraMode} zoom={props.zoom} focusTarget={props.focusTarget} reducedMotion={props.reducedMotion} />
      <OrbitControls enabled={props.cameraMode === "overview"} enablePan enableRotate enableZoom minDistance={8} maxDistance={25} maxPolarAngle={Math.PI / 2.15} target={props.focusTarget} />
    </>
  );
}

export function OfficeScene(props: OfficeSceneProps) {
  return (
    <Canvas
      camera={{ position: [7, 14, 7], fov: 42, near: 0.1, far: 80 }}
      dpr={[1, 1.5]}
      gl={{ antialias: false, alpha: false, powerPreference: "low-power" }}
      shadows
      tabIndex={0}
      aria-label="Interactive low-poly office template. Focus the scene to move with WASD or arrow keys; click a clear floor tile to set a route."
      onPointerDown={(event) => event.currentTarget.focus()}
    >
      <SceneContents props={props} />
    </Canvas>
  );
}
