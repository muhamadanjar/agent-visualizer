"use client";

import { memo, useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import type { RefObject } from "react";
import { Canvas, useFrame, useThree } from "@react-three/fiber";
import { CapsuleCollider, CuboidCollider, Physics, RigidBody, useRapier } from "@react-three/rapier";
import type { RapierRigidBody } from "@react-three/rapier";
import type { KinematicCharacterController } from "@dimforge/rapier3d-compat";
import { InstancedMesh, Object3D, Vector3 } from "three";

const FLOOR_EDGE = 6;
const GRID_MIN = -5.5;
const GRID_COUNT = 12;
const CELL_SIZE = 1;
const MOVE_SPEED = 3.2;
const CHARACTER_RADIUS = 0.32;

// Adaptive render scale: the browser measures its own frame budget, so the scene
// lowers its resolution on slow hardware instead of assuming a target device.
const RENDER_SCALE_MIN = 0.25;
const RENDER_SCALE_MAX = 1.5;
const RENDER_SCALE_STEP = 0.25;
const SCALE_DOWN_FPS = 40;
const SCALE_UP_FPS = 55;
const FPS_WINDOW = 0.75;

// ponytail: Lambert materials match Standard here because the scene lights with a
// single ambient light and no shadow-casting light. Restore Standard materials when
// a directional or specular light is added.

const BLOCKERS = [
  { id: "desk", x: -2, z: -2, halfX: 1, halfZ: 0.55, height: 0.8, color: "#8d6749" },
  { id: "meeting-table", x: 1.6, z: 1.1, halfX: 1.05, halfZ: 0.72, height: 0.58, color: "#657b83" },
  { id: "cabinet", x: -3.8, z: 2.4, halfX: 0.48, halfZ: 0.95, height: 1.25, color: "#526273" },
] as const;

const NEIGHBORS = [
  { x: 1, z: 0, cost: 1 },
  { x: -1, z: 0, cost: 1 },
  { x: 0, z: 1, cost: 1 },
  { x: 0, z: -1, cost: 1 },
  { x: 1, z: 1, cost: Math.SQRT2 },
  { x: 1, z: -1, cost: Math.SQRT2 },
  { x: -1, z: 1, cost: Math.SQRT2 },
  { x: -1, z: -1, cost: Math.SQRT2 },
] as const;
const TEST_AVATAR_POSITIONS = Array.from({ length: 50 }, (_, index) => ({
  x: -4.5 + (index % 10),
  z: -4.5 + Math.floor(index / 10) * 2.25,
}));

function SyntheticAvatars() {
  const instances = useRef<InstancedMesh>(null);
  const transform = useMemo(() => new Object3D(), []);

  useLayoutEffect(() => {
    const mesh = instances.current;
    if (!mesh) return;
    for (let index = 0; index < TEST_AVATAR_POSITIONS.length; index += 1) {
      const { x, z } = TEST_AVATAR_POSITIONS[index];
      transform.position.set(x, 0.48, z);
      transform.updateMatrix();
      mesh.setMatrixAt(index, transform.matrix);
    }
    mesh.instanceMatrix.needsUpdate = true;
  }, [transform]);

  return (
    <instancedMesh ref={instances} args={[undefined, undefined, TEST_AVATAR_POSITIONS.length]} castShadow>
      <capsuleGeometry args={[0.16, 0.5, 4, 8]} />
      <meshLambertMaterial color="#ad83d4" />
    </instancedMesh>
  );
}

type Cell = { x: number; z: number };
type CameraMode = "follow" | "overview";
type OfficeCanvasProps = {
  cameraMode: CameraMode;
  loadTest: boolean;
  onStatus: (message: string) => void;
  onFps: (value: number) => void;
  onMotionMs: (value: number) => void;
  onCell: (value: string) => void;
  onScale: (value: number) => void;
  onCalls: (value: number) => void;
};
type MoveTo = (x: number, z: number) => void;

function cellKey(cell: Cell): string {
  return `${cell.x},${cell.z}`;
}

function worldToCell(x: number, z: number): Cell | null {
  const column = Math.round((x - GRID_MIN) / CELL_SIZE);
  const row = Math.round((z - GRID_MIN) / CELL_SIZE);
  if (column < 0 || column >= GRID_COUNT || row < 0 || row >= GRID_COUNT) return null;
  return { x: column, z: row };
}

function cellToWorld(cell: Cell): Vector3 {
  return new Vector3(GRID_MIN + cell.x * CELL_SIZE, 0, GRID_MIN + cell.z * CELL_SIZE);
}

function isBlocked(cell: Cell): boolean {
  const x = GRID_MIN + cell.x * CELL_SIZE;
  const z = GRID_MIN + cell.z * CELL_SIZE;
  return BLOCKERS.some((blocker) =>
    Math.abs(x - blocker.x) <= blocker.halfX + CHARACTER_RADIUS &&
    Math.abs(z - blocker.z) <= blocker.halfZ + CHARACTER_RADIUS,
  );
}

function heuristic(a: Cell, b: Cell): number {
  const dx = Math.abs(a.x - b.x);
  const dz = Math.abs(a.z - b.z);
  return Math.max(dx, dz) + (Math.SQRT2 - 1) * Math.min(dx, dz);
}

function findPath(start: Cell, goal: Cell): Cell[] | null {
  if (isBlocked(start) || isBlocked(goal)) return null;
  const open = [start];
  const cameFrom = new Map<string, Cell>();
  const costFromStart = new Map<string, number>([[cellKey(start), 0]]);
  const score = new Map<string, number>([[cellKey(start), heuristic(start, goal)]]);
  const closed = new Set<string>();

  while (open.length > 0) {
    open.sort((a, b) => (score.get(cellKey(a)) ?? Infinity) - (score.get(cellKey(b)) ?? Infinity));
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
      if (next.x < 0 || next.x >= GRID_COUNT || next.z < 0 || next.z >= GRID_COUNT || isBlocked(next) || closed.has(nextKey)) continue;
      if (direction.x !== 0 && direction.z !== 0) {
        if (isBlocked({ x: current.x + direction.x, z: current.z }) || isBlocked({ x: current.x, z: current.z + direction.z })) continue;
      }
      const tentative = (costFromStart.get(currentKey) ?? Infinity) + direction.cost;
      if (tentative >= (costFromStart.get(nextKey) ?? Infinity)) continue;
      cameFrom.set(nextKey, current);
      costFromStart.set(nextKey, tentative);
      score.set(nextKey, tentative + heuristic(next, goal));
      if (!open.some((cell) => cellKey(cell) === nextKey)) open.push(next);
    }
  }
  return null;
}

function Furniture({ position, halfX, halfZ, height, color, onBlockedClick }: {
  position: [number, number, number];
  halfX: number;
  halfZ: number;
  height: number;
  color: string;
  onBlockedClick: () => void;
}) {
  return (
    <RigidBody type="fixed" colliders={false} position={position}>
      <CuboidCollider args={[halfX, height / 2, halfZ]} position={[0, height / 2, 0]} />
      <mesh
        position={[0, height / 2, 0]}
        castShadow
        receiveShadow
        onPointerDown={(event) => {
          event.stopPropagation();
          onBlockedClick();
        }}
      >
        <boxGeometry args={[halfX * 2, height, halfZ * 2]} />
        <meshLambertMaterial color={color} />
      </mesh>
    </RigidBody>
  );
}

function Player({ cameraMode, onStatus, onMotionMs, onInteract, onCell, bodyRef, moveToRef }: {
  cameraMode: CameraMode;
  onStatus: OfficeCanvasProps["onStatus"];
  onMotionMs: OfficeCanvasProps["onMotionMs"];
  onInteract: () => void;
  onCell: OfficeCanvasProps["onCell"];
  bodyRef: RefObject<RapierRigidBody | null>;
  moveToRef: RefObject<MoveTo>;
}) {
  const { world } = useRapier();
  const controllerRef = useRef<KinematicCharacterController | null>(null);
  const keysRef = useRef(new Set<string>());
  const routeRef = useRef<Vector3[]>([]);
  const desiredRef = useRef(new Vector3());
  const lastInputAtRef = useRef<number | null>(null);
  const lastRouteStatusRef = useRef(0);
  const lastColumnRef = useRef<number | null>(null);
  const lastRowRef = useRef<number | null>(null);
  const nextTranslationRef = useRef({ x: 0, y: 0, z: 0 });

  useEffect(() => {
    const controller = world.createCharacterController(0.02);
    controller.setUp({ x: 0, y: 1, z: 0 });
    controller.setSlideEnabled(true);
    controllerRef.current = controller;
    return () => {
      world.removeCharacterController(controller);
      controllerRef.current = null;
    };
  }, [world]);

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      const target = event.target;
      if (target instanceof HTMLElement && (target.isContentEditable || /^(INPUT|TEXTAREA|BUTTON|SELECT)$/.test(target.tagName))) return;
      const key = event.key.toLowerCase();
      if (["w", "a", "s", "d", "arrowup", "arrowdown", "arrowleft", "arrowright"].includes(key)) {
        event.preventDefault();
        if (!keysRef.current.has(key)) {
          keysRef.current.add(key);
          routeRef.current = [];
          lastInputAtRef.current = performance.now();
          onStatus("Manual movement active; click-to-move route canceled.");
        }
      }
      if (key === "e") {
        const position = bodyRef.current?.translation();
        if (position && Math.hypot(position.x - 4, position.z + 4) <= 1.55) onInteract();
      }
    };
    const onKeyUp = (event: KeyboardEvent) => keysRef.current.delete(event.key.toLowerCase());
    const onBlur = () => keysRef.current.clear();
    window.addEventListener("keydown", onKeyDown);
    window.addEventListener("keyup", onKeyUp);
    window.addEventListener("blur", onBlur);
    return () => {
      window.removeEventListener("keydown", onKeyDown);
      window.removeEventListener("keyup", onKeyUp);
      window.removeEventListener("blur", onBlur);
    };
  }, [bodyRef, onInteract, onStatus]);

  const moveTo = useCallback((x: number, z: number) => {
    const position = bodyRef.current?.translation();
    const start = position ? worldToCell(position.x, position.z) : null;
    const goal = worldToCell(x, z);
    if (!start || !goal) {
      routeRef.current = [];
      onStatus("Invalid destination: outside the walkable floor.");
      return;
    }
    const path = findPath(start, goal);
    if (!path) {
      routeRef.current = [];
      onStatus(`No route: destination ${cellKey(goal)} is blocked or unreachable.`);
      return;
    }
    routeRef.current = path.map(cellToWorld);
    onStatus(path.length === 0 ? "Already at this destination." : `Route set: ${path.length} grid steps.`);
  }, [bodyRef, onStatus]);
  moveToRef.current = moveTo;

  useFrame((_, delta) => {
    const body = bodyRef.current;
    const controller = controllerRef.current;
    if (!body || !controller) return;
    const position = body.translation();
    const column = Math.round((position.x - GRID_MIN) / CELL_SIZE);
    const row = Math.round((position.z - GRID_MIN) / CELL_SIZE);
    const inside = column >= 0 && column < GRID_COUNT && row >= 0 && row < GRID_COUNT;
    if (column !== lastColumnRef.current || row !== lastRowRef.current) {
      lastColumnRef.current = column;
      lastRowRef.current = row;
      onCell(inside ? `${column},${row}` : "outside");
    }

    const keys = keysRef.current;
    let dx = (keys.has("d") || keys.has("arrowright") ? 1 : 0) - (keys.has("a") || keys.has("arrowleft") ? 1 : 0);
    let dz = (keys.has("s") || keys.has("arrowdown") ? 1 : 0) - (keys.has("w") || keys.has("arrowup") ? 1 : 0);
    const manual = dx !== 0 || dz !== 0;
    const followingRoute = !manual && routeRef.current.length > 0;

    if (!manual && routeRef.current.length > 0) {
      let waypoint = routeRef.current[0];
      while (waypoint && Math.hypot(waypoint.x - position.x, waypoint.z - position.z) < 0.16) {
        routeRef.current.shift();
        waypoint = routeRef.current[0];
      }
      if (waypoint) {
        dx = waypoint.x - position.x;
        dz = waypoint.z - position.z;
      }
    }
    if (followingRoute && routeRef.current.length === 0) {
      onStatus("Arrived at the route destination.");
      return;
    }

    const length = Math.hypot(dx, dz);
    if (length === 0) return;
    dx /= length;
    dz /= length;
    const step = Math.min(delta, 0.05) * MOVE_SPEED;
    const desired = desiredRef.current.set(dx * step, 0, dz * step);
    controller.computeColliderMovement(body.collider(0), desired);
    const actual = controller.computedMovement();
    if (actual.x === 0 && actual.z === 0) {
      if (routeRef.current.length > 0 && performance.now() - lastRouteStatusRef.current > 600) {
        lastRouteStatusRef.current = performance.now();
        routeRef.current = [];
        onStatus("Route stopped at a solid collider.");
      }
      return;
    }
    const next = nextTranslationRef.current;
    next.x = position.x + actual.x;
    next.y = position.y + actual.y;
    next.z = position.z + actual.z;
    body.setNextKinematicTranslation(next);
    if (lastInputAtRef.current !== null) {
      onMotionMs(Math.max(0, Math.round(performance.now() - lastInputAtRef.current)));
      lastInputAtRef.current = null;
    }
  });

  return (
    <>
      <RigidBody
        ref={bodyRef}
        type="kinematicPosition"
        colliders={false}
        position={[0, 0.72, 4]}
        enabledRotations={[false, false, false]}
        enabledTranslations={[true, false, true]}
      >
        <CapsuleCollider args={[0.35, 0.28]} />
        <mesh position={[0, 0.02, 0]} castShadow>
          <capsuleGeometry args={[0.28, 0.7, 6, 12]} />
          <meshLambertMaterial color="#6ee7c2" />
        </mesh>
        <mesh position={[0, 0.43, 0.23]}>
          <sphereGeometry args={[0.055, 12, 8]} />
          <meshLambertMaterial color="#10251f" />
        </mesh>
      </RigidBody>
      <CameraRig bodyRef={bodyRef} mode={cameraMode} />
    </>
  );
}

function CameraRig({ bodyRef, mode }: { bodyRef: RefObject<RapierRigidBody | null>; mode: CameraMode }) {
  const desired = useMemo(() => new Vector3(), []);
  const target = useMemo(() => new Vector3(), []);
  const reducedMotion = useRef(false);

  useEffect(() => {
    const query = window.matchMedia("(prefers-reduced-motion: reduce)");
    const updatePreference = () => { reducedMotion.current = query.matches; };
    updatePreference();
    query.addEventListener("change", updatePreference);
    return () => query.removeEventListener("change", updatePreference);
  }, []);

  useFrame(({ camera }, delta) => {
    const position = bodyRef.current?.translation();
    if (!position) return;
    const overview = mode === "overview";
    target.set(position.x, 0, position.z);
    desired.set(position.x + (overview ? 0 : 7.5), position.y + (overview ? 15 : 8), position.z + (overview ? 0.01 : 7.5));
    camera.up.set(0, overview ? 0 : 1, overview ? -1 : 0);
    if (reducedMotion.current) camera.position.copy(desired);
    else camera.position.lerp(desired, 1 - Math.exp(-delta * 5));
    camera.lookAt(target);
  });
  return null;
}

function Floor({ onMove }: { onMove: (x: number, z: number) => void }) {
  return (
    <>
      <RigidBody type="fixed" colliders={false}>
        <CuboidCollider position={[0, -0.1, 0]} args={[FLOOR_EDGE, 0.1, FLOOR_EDGE]} />
      </RigidBody>
      <mesh
        rotation={[-Math.PI / 2, 0, 0]}
        position={[0, 0, 0]}
        receiveShadow
        onPointerDown={(event) => {
          event.stopPropagation();
          onMove(event.point.x, event.point.z);
        }}
      >
        <planeGeometry args={[FLOOR_EDGE * 2, FLOOR_EDGE * 2]} />
        <meshLambertMaterial color="#d8d5ca" />
      </mesh>
      <gridHelper args={[GRID_COUNT, GRID_COUNT, "#7a8c88", "#bdc2b8"]} position={[0, 0.012, 0]} />
    </>
  );
}

function InteractionMarker({ onInteract }: { onInteract: () => void }) {
  return (
    <group position={[4, 0, -4]} onClick={(event) => { event.stopPropagation(); onInteract(); }}>
      <mesh position={[0, 0.48, 0]} castShadow>
        <boxGeometry args={[0.72, 0.96, 0.62]} />
        <meshLambertMaterial color="#54b7ec" emissive="#123448" />
      </mesh>
      <mesh position={[0, 1.02, 0]}>
        <sphereGeometry args={[0.1, 12, 8]} />
        <meshLambertMaterial color="#d8f3ff" emissive="#438db5" />
      </mesh>
    </group>
  );
}

function SceneDiagnostics({ onFps, onScale, onCalls, onScaleRequest }: {
  onFps: OfficeCanvasProps["onFps"];
  onScale: OfficeCanvasProps["onScale"];
  onCalls: OfficeCanvasProps["onCalls"];
  onScaleRequest: (scale: number) => void;
}) {
  const gl = useThree((state) => state.gl);
  const appliedDpr = useThree((state) => state.viewport.dpr);
  const sample = useRef({ frames: 0, elapsed: 0, goodWindows: 0, cooldown: 0, reported: -1 });

  useFrame((_, delta) => {
    const state = sample.current;
    state.frames += 1;
    state.elapsed += delta;
    if (state.elapsed < FPS_WINDOW) return;
    const fps = state.frames / state.elapsed;
    state.frames = 0;
    state.elapsed = 0;
    onFps(Math.round(fps));
    onCalls(gl.info.render.calls);

    const ceiling = Math.min(Math.max(window.devicePixelRatio, 1), RENDER_SCALE_MAX);
    const scale = Math.min(Math.max(appliedDpr, RENDER_SCALE_MIN), ceiling);
    let nextScale = scale;
    if (state.cooldown > 0) {
      state.cooldown -= 1;
    } else if (document.visibilityState === "visible") {
      if (fps < SCALE_DOWN_FPS && scale > RENDER_SCALE_MIN) {
        nextScale = Math.max(RENDER_SCALE_MIN, scale - RENDER_SCALE_STEP);
        state.goodWindows = 0;
      } else if (fps > SCALE_UP_FPS && scale < ceiling) {
        state.goodWindows += 1;
        if (state.goodWindows >= 2) {
          nextScale = Math.min(ceiling, scale + RENDER_SCALE_STEP);
          state.goodWindows = 0;
        }
      } else {
        state.goodWindows = 0;
      }
    }
    if (Math.abs(appliedDpr - nextScale) > 0.01) {
      onScaleRequest(nextScale);
      state.cooldown = 1;
    }
    if (Math.abs(state.reported - appliedDpr) > 0.01) {
      state.reported = appliedDpr;
      onScale(appliedDpr);
    }
  });
  return null;
}

function OfficeCanvas({ cameraMode, loadTest, onStatus, onFps, onMotionMs, onCell, onScale, onCalls }: OfficeCanvasProps) {
  const bodyRef = useRef<RapierRigidBody>(null);
  const [renderScale, setRenderScale] = useState(1);
  useEffect(() => {
    setRenderScale(Math.min(Math.max(window.devicePixelRatio, 1), RENDER_SCALE_MAX));
  }, []);
  const moveToRef = useRef<MoveTo>(() => {});
  const interact = useCallback(() => onStatus("Console selected. This spike has no agent or runtime connection."), [onStatus]);

  return (
    <Canvas
      camera={{ position: [7.5, 8.72, 11.5], fov: 42, near: 0.1, far: 100 }}
      dpr={renderScale}
      gl={{ antialias: false, alpha: false, powerPreference: "low-power" }}
      aria-label="3D office movement and collision test"
    >
      <color attach="background" args={["#111a20"]} />
      <ambientLight intensity={1.2} />
      {loadTest && <SyntheticAvatars />}
      <Physics gravity={[0, -9.81, 0]} colliders={false} timeStep="vary">
        <Floor onMove={(x, z) => moveToRef.current(x, z)} />
        {BLOCKERS.map((blocker) => (
          <Furniture
            key={blocker.id}
            position={[blocker.x, 0, blocker.z]}
            halfX={blocker.halfX}
            halfZ={blocker.halfZ}
            height={blocker.height}
            color={blocker.color}
            onBlockedClick={() => moveToRef.current(blocker.x, blocker.z)}
          />
        ))}
        <RigidBody type="fixed" colliders={false}>
          <CuboidCollider position={[0, 1, -FLOOR_EDGE]} args={[FLOOR_EDGE, 1, 0.16]} />
          <CuboidCollider position={[0, 1, FLOOR_EDGE]} args={[FLOOR_EDGE, 1, 0.16]} />
          <CuboidCollider position={[-FLOOR_EDGE, 1, 0]} args={[0.16, 1, FLOOR_EDGE]} />
          <CuboidCollider position={[FLOOR_EDGE, 1, 0]} args={[0.16, 1, FLOOR_EDGE]} />
          <mesh position={[0, 0.4, -FLOOR_EDGE]}><boxGeometry args={[12, 0.8, 0.32]} /><meshLambertMaterial color="#53615e" /></mesh>
          <mesh position={[0, 0.4, FLOOR_EDGE]}><boxGeometry args={[12, 0.8, 0.32]} /><meshLambertMaterial color="#53615e" /></mesh>
          <mesh position={[-FLOOR_EDGE, 0.4, 0]}><boxGeometry args={[0.32, 0.8, 12]} /><meshLambertMaterial color="#53615e" /></mesh>
          <mesh position={[FLOOR_EDGE, 0.4, 0]}><boxGeometry args={[0.32, 0.8, 12]} /><meshLambertMaterial color="#53615e" /></mesh>
        </RigidBody>
        <InteractionMarker onInteract={interact} />
        <Player cameraMode={cameraMode} onStatus={onStatus} onMotionMs={onMotionMs} onInteract={interact} onCell={onCell} bodyRef={bodyRef} moveToRef={moveToRef} />
      </Physics>
      <SceneDiagnostics onFps={onFps} onScale={onScale} onCalls={onCalls} onScaleRequest={setRenderScale} />
    </Canvas>
  );
}

export default memo(OfficeCanvas);
