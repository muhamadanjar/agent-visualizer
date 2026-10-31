"use client";

import { Html, Instance, Instances, OrbitControls, useAnimations, useGLTF } from "@react-three/drei";
import { Canvas, useFrame, useThree } from "@react-three/fiber";
import { Suspense, useEffect, useMemo, useRef, useState, type CSSProperties } from "react";
import { LoopOnce, LoopRepeat, Mesh, MeshStandardMaterial } from "three";
import type { Group, Object3D } from "three";
import * as SkeletonUtils from "three/addons/utils/SkeletonUtils.js";
import type { AgentAvailabilityState } from "../lib/activity";
import { OFFICE_AVATAR_POSE_BONES } from "./office-avatar";
import { createOfficeLayout, type OfficeLayout, type OfficePosition } from "./office-layout";

type AgentAnimation = "working" | "success" | "neutral";
export type OfficeSceneAgent = {
  id: string;
  displayName: string;
  statusLabel: string;
  animation: AgentAnimation;
  availability: AgentAvailabilityState;
  modelUrl: string;
  fullAvatar: boolean;
  selected: boolean;
  accent: string;
  position: OfficePosition;
  stationPosition: OfficePosition;
  chairPosition: OfficePosition;
  restPosition: OfficePosition;
  restSeatPosition: OfficePosition;
  routeToWork: OfficePosition[];
  routeToRest: OfficePosition[];
};

type OfficeSceneProps = {
  agents: OfficeSceneAgent[];
  zoom: number;
  focusTarget: [number, number, number];
  reducedMotion: boolean;
  simplified: boolean;
  onSelectAgent: (agentId: string) => void;
};

const CAMERA_FOV = 42;

function AvatarLabel({ agent }: { agent: OfficeSceneAgent }) {
  return (
    <Html position={[0, 2.05, 0]} center distanceFactor={16} style={{ pointerEvents: "none" }}>
      <span className="scene-avatar-label" style={{ "--avatar-accent": agent.accent } as CSSProperties}>
        <strong>{agent.displayName}</strong>
        <small>{agent.statusLabel}</small>
      </span>
    </Html>
  );
}

type BonePose = {
  object: Object3D;
  rotationX: number;
  rotationY: number;
  rotationZ: number;
  positionY: number;
};

function captureBone(model: Object3D, name: string): BonePose | null {
  const object = model.getObjectByName(name);
  if (!object) return null;
  return {
    object,
    rotationX: object.rotation.x,
    rotationY: object.rotation.y,
    rotationZ: object.rotation.z,
    positionY: object.position.y,
  };
}

function applyBonePose(
  pose: BonePose | null,
  offsetX = 0,
  offsetY = 0,
  offsetZ = 0,
  offsetPositionY = 0,
) {
  if (!pose) return;
  pose.object.rotation.set(
    pose.rotationX + offsetX,
    pose.rotationY + offsetY,
    pose.rotationZ + offsetZ,
  );
  pose.object.position.y = pose.positionY + offsetPositionY;
}

function AnimatedAgent({ agent, reducedMotion, onSelectAgent }: {
  agent: OfficeSceneAgent;
  reducedMotion: boolean;
  onSelectAgent: (agentId: string) => void;
}) {
  const { scene, animations } = useGLTF(agent.modelUrl, false, false);
  const cubeWorld = agent.modelUrl.includes("/Cube_");
  const groupRef = useRef<Group>(null);
  const [hovered, setHovered] = useState(false);
  const [walking, setWalking] = useState(false);
  const [successReactionFinished, setSuccessReactionFinished] = useState(false);
  const idleRoutineRef = useRef<{
    phase: "seated" | "outbound" | "away" | "returning";
    nextAt: number;
  }>({ phase: "seated", nextAt: 2 + [...agent.id].reduce((sum, char) => sum + char.charCodeAt(0), 0) % 5 });
  const mountedRef = useRef(false);
  const availabilityRef = useRef(agent.availability);
  const initialPosition = useRef<OfficePosition>(
    agent.availability === "idle" ? agent.restPosition : agent.chairPosition,
  );
  const initialRotation = useRef<OfficePosition>(
    [0, agent.availability === "idle" ? 0 : Math.PI, 0],
  );
  const waypointsRef = useRef<OfficePosition[]>([]);
  const waypointIndexRef = useRef(0);
  const model = useMemo(() => {
    const clone = SkeletonUtils.clone(scene);
    clone.traverse((object: Object3D) => {
      if (!(object instanceof Mesh)) return;
      const applyAccent = (material: MeshStandardMaterial) => {
        if (material.name !== "Tie") return material;
        const variant = material.clone();
        variant.name = "Agent accent";
        variant.color.set(agent.accent);
        return variant;
      };
      object.material = Array.isArray(object.material)
        ? object.material.map((material) => material instanceof MeshStandardMaterial ? applyAccent(material) : material)
        : object.material instanceof MeshStandardMaterial
          ? applyAccent(object.material)
          : object.material;
    });
    return clone;
  }, [agent.accent, scene]);
  const bones = useMemo(() => ({
    hips: captureBone(model, OFFICE_AVATAR_POSE_BONES.hips),
    body: captureBone(model, OFFICE_AVATAR_POSE_BONES.body),
    upperLegLeft: captureBone(model, OFFICE_AVATAR_POSE_BONES.upperLegLeft),
    lowerLegLeft: captureBone(model, OFFICE_AVATAR_POSE_BONES.lowerLegLeft),
    upperLegRight: captureBone(model, OFFICE_AVATAR_POSE_BONES.upperLegRight),
    lowerLegRight: captureBone(model, OFFICE_AVATAR_POSE_BONES.lowerLegRight),
    upperArmLeft: captureBone(model, OFFICE_AVATAR_POSE_BONES.upperArmLeft),
    lowerArmLeft: captureBone(model, OFFICE_AVATAR_POSE_BONES.lowerArmLeft),
    upperArmRight: captureBone(model, OFFICE_AVATAR_POSE_BONES.upperArmRight),
    lowerArmRight: captureBone(model, OFFICE_AVATAR_POSE_BONES.lowerArmRight),
    wristLeft: captureBone(model, cubeWorld ? "FistL" : OFFICE_AVATAR_POSE_BONES.wristLeft),
    wristRight: captureBone(model, cubeWorld ? "FistR" : OFFICE_AVATAR_POSE_BONES.wristRight),
  }), [model, cubeWorld]);
  const { actions, mixer } = useAnimations(animations, groupRef);

  useEffect(() => () => {
    model.traverse((object: Object3D) => {
      if (!(object instanceof Mesh)) return;
      const materials = Array.isArray(object.material) ? object.material : [object.material];
      for (const material of materials) {
        if (material.name === "Agent accent") material.dispose();
      }
    });
  }, [model]);

  useEffect(() => {
    if (!mountedRef.current) {
      mountedRef.current = true;
      return;
    }

    if (reducedMotion) {
      const target = agent.availability === "idle" ? agent.restPosition : agent.chairPosition;
      groupRef.current?.position.set(target[0], target[1], target[2]);
      if (groupRef.current) groupRef.current.rotation.y = agent.availability === "idle" ? 0 : Math.PI;
      waypointsRef.current = [];
      waypointIndexRef.current = 0;
      idleRoutineRef.current.phase = "seated";
      idleRoutineRef.current.nextAt = 0;
      setWalking(false);
    }

    if (availabilityRef.current === agent.availability) return;
    const previousAvailability = availabilityRef.current;
    availabilityRef.current = agent.availability;
    idleRoutineRef.current.phase = "seated";
    idleRoutineRef.current.nextAt = 0;
    if ((previousAvailability === "idle") === (agent.availability === "idle")) return;

    const route = agent.availability === "idle" ? agent.routeToRest : agent.routeToWork;
    if (reducedMotion) {
      const target = route[route.length - 1];
      if (target) groupRef.current?.position.set(target[0], target[1], target[2]);
      waypointsRef.current = [];
      waypointIndexRef.current = 0;
      setWalking(false);
      return;
    }
    const position = groupRef.current?.position;
    if (position) {
      waypointsRef.current = [[position.x, position.y, position.z], ...route];
      waypointIndexRef.current = 1;
    } else {
      waypointsRef.current = route;
      waypointIndexRef.current = 0;
    }
    setWalking(route.length > 0);
  }, [agent.availability, agent.routeToRest, agent.routeToWork, reducedMotion]);

  useEffect(() => {
    setSuccessReactionFinished(false);
  }, [agent.animation]);

  const clip = walking ? (cubeWorld ? "Walk" : "CharacterArmature|Walk")
    : agent.animation === "success" && !successReactionFinished ? (cubeWorld ? "Wave" : "CharacterArmature|Wave") : null;
  useEffect(() => {
    if (!clip || reducedMotion) return;
    const action = actions[clip];
    if (!action) return;
    const isSuccess = clip === (cubeWorld ? "Wave" : "CharacterArmature|Wave");
    const onFinished = () => setSuccessReactionFinished(true);
    if (isSuccess) mixer.addEventListener("finished", onFinished);
    action.reset();
    action.enabled = true;
    action.clampWhenFinished = isSuccess;
    action.setLoop(isSuccess ? LoopOnce : LoopRepeat, isSuccess ? 1 : Infinity);
    action.fadeIn(0.18).play();
    return () => {
      if (isSuccess) mixer.removeEventListener("finished", onFinished);
      action.fadeOut(0.12);
    };
  }, [actions, clip, mixer, reducedMotion, cubeWorld]);

  useFrame((state, delta) => {
    const group = groupRef.current;
    if (!group) return;
    const routine = idleRoutineRef.current;
    if (agent.availability === "idle" && !reducedMotion && waypointsRef.current.length === 0) {
      if (routine.nextAt === 0) routine.nextAt = state.clock.elapsedTime + 3;
      if (state.clock.elapsedTime >= routine.nextAt) {
        if (routine.phase === "seated") {
          routine.phase = "outbound";
          waypointsRef.current = [[agent.restPosition[0], agent.restPosition[1], agent.restPosition[2] + 0.85]];
          waypointIndexRef.current = 0;
          setWalking(true);
        } else if (routine.phase === "away") {
          routine.phase = "returning";
          waypointsRef.current = [agent.restPosition];
          waypointIndexRef.current = 0;
          setWalking(true);
        }
      }
    }
    const route = waypointsRef.current;
    if (route.length > 0) {
      const target = route[waypointIndexRef.current];
      if (!target) {
        waypointsRef.current = [];
        waypointIndexRef.current = 0;
        if (routine.phase === "outbound") {
          routine.phase = "away";
          routine.nextAt = state.clock.elapsedTime + 3;
        } else if (routine.phase === "returning") {
          routine.phase = "seated";
          routine.nextAt = state.clock.elapsedTime + 12;
        }
        group.rotation.y = agent.availability === "idle" ? 0 : Math.PI;
        setWalking(false);
      } else {
        const dx = target[0] - group.position.x;
        const dy = target[1] - group.position.y;
        const dz = target[2] - group.position.z;
        const distance = Math.hypot(dx, dy, dz);
        if (distance < 0.08) {
          waypointIndexRef.current += 1;
        } else {
          const speed = Math.min(2.6, Math.max(0.65, distance * 3));
          const step = Math.min(distance, delta * speed);
          group.position.x += (dx / distance) * step;
          group.position.y += (dy / distance) * step;
          group.position.z += (dz / distance) * step;
          group.rotation.y = Math.atan2(dx, dz);
        }
      }
      return;
    }

    if (agent.availability === "idle" && routine.phase === "away" && !reducedMotion) {
      for (const bone of Object.values(bones)) applyBonePose(bone);
      return;
    }

    if (agent.animation === "success" && !successReactionFinished && !reducedMotion) return;
    const typing = agent.availability === "working";
    const relaxed = agent.availability === "idle";
    const legBend = relaxed ? 1.32 : 1.38;
    const typingMotion = typing && !reducedMotion ? Math.sin(state.clock.elapsedTime * 13) * 0.08 : 0;
    applyBonePose(bones.hips);
    applyBonePose(bones.upperLegLeft, legBend, 0, 0);
    applyBonePose(bones.lowerLegLeft, -1.1, 0, 0);
    applyBonePose(bones.upperLegRight, legBend, 0, 0);
    applyBonePose(bones.lowerLegRight, -1.1, 0, 0);
    applyBonePose(bones.upperArmLeft, 0, 0, -0.18);
    applyBonePose(bones.body, typing ? 0.12 : -0.08, 0, reducedMotion ? 0 : Math.sin(state.clock.elapsedTime * 1.2) * 0.015);
    applyBonePose(bones.lowerArmLeft, -0.42 + typingMotion, 0, 0);
    applyBonePose(bones.upperArmRight, 0, 0, 0.18);
    applyBonePose(bones.lowerArmRight, -0.42 - typingMotion, 0, 0);
    applyBonePose(bones.wristLeft, typing && !reducedMotion ? Math.sin(state.clock.elapsedTime * 17) * 0.08 : 0, 0, 0);
    applyBonePose(bones.wristRight, typing && !reducedMotion ? Math.sin(state.clock.elapsedTime * 17 + Math.PI) * 0.08 : 0, 0, 0);
  });

  return (
    <group
      ref={groupRef}
      position={initialPosition.current}
      rotation={initialRotation.current}
      scale={0.78}
      onClick={(event) => { event.stopPropagation(); onSelectAgent(agent.id); }}
      onPointerOver={() => setHovered(true)}
      onPointerOut={() => setHovered(false)}
    >
      <primitive object={model} scale={cubeWorld ? 0.674 : 1} />
      <mesh position={[0, 0.025, 0]} rotation={[-Math.PI / 2, 0, 0]}>
        <ringGeometry args={[0.43, 0.51, 24]} />
        <meshBasicMaterial color={agent.accent} />
      </mesh>
      {agent.selected || hovered ? <AvatarLabel agent={agent} /> : null}
    </group>
  );
}

function StaticAgentMarkers({ agents, onSelectAgent }: {
  agents: OfficeSceneAgent[];
  onSelectAgent: (agentId: string) => void;
}) {
  if (!agents.length) return null;
  return (
    <Instances limit={agents.length} range={agents.length}>
      <dodecahedronGeometry args={[0.24, 0]} />
      <meshStandardMaterial roughness={0.62} />
      {agents.map((agent) => (
        <Instance
          key={agent.id}
          position={[agent.position[0], 0.25, agent.position[2]]}
          color={agent.accent}
          onClick={(event) => { event.stopPropagation(); onSelectAgent(agent.id); }}
        />
      ))}
    </Instances>
  );
}

function SelectedMarkerLabel({ agent }: { agent: OfficeSceneAgent }) {
  return (
    <group position={[agent.position[0], 0, agent.position[2]]}>
      <Html position={[0, 0.7, 0]} center distanceFactor={16} style={{ pointerEvents: "none" }}>
        <span className="scene-avatar-label" style={{ "--avatar-accent": agent.accent } as CSSProperties}>
          <strong>{agent.displayName}</strong>
          <small>{agent.statusLabel}</small>
        </span>
      </Html>
    </group>
  );
}

const CAMERA_OFFSET_LENGTH = Math.hypot(0.48, 0.86, 0.62);
function CameraRig({ zoom, focusTarget }: { zoom: number; focusTarget: [number, number, number] }) {
  const { camera } = useThree();
  useEffect(() => {
    camera.position.set(
      focusTarget[0] + zoom * 0.48 / CAMERA_OFFSET_LENGTH,
      focusTarget[1] + zoom * 0.86 / CAMERA_OFFSET_LENGTH,
      focusTarget[2] + zoom * 0.62 / CAMERA_OFFSET_LENGTH,
    );
    camera.up.set(0, 1, 0);
    camera.lookAt(...focusTarget);
    camera.updateProjectionMatrix();
  }, [camera, focusTarget, zoom]);
  return null;
}

type FurnitureItem = { position: OfficePosition; rotation?: OfficePosition };

function InstancedBoxes({ items, dimensions, color, unlit = false }: {
  items: FurnitureItem[];
  dimensions: OfficePosition;
  color: string;
  unlit?: boolean;
}) {
  if (!items.length) return null;
  return (
    <Instances limit={items.length} range={items.length}>
      <boxGeometry args={dimensions} />
      {unlit ? <meshBasicMaterial color={color} /> : <meshLambertMaterial color={color} />}
      {items.map((item, index) => (
        <Instance key={index} position={item.position} rotation={item.rotation} />
      ))}
    </Instances>
  );
}

function OfficeWorkstations({ agents }: { agents: OfficeSceneAgent[] }) {
  if (!agents.length) return null;
  const item = (x: number, y: number, z: number): FurnitureItem => ({ position: [x, y, z] });
  const desktops: FurnitureItem[] = [];
  const deskLegs: FurnitureItem[] = [];
  const monitorFrames: FurnitureItem[] = [];
  const monitorStands: FurnitureItem[] = [];
  const monitorScreens: FurnitureItem[] = [];
  const keyboards: FurnitureItem[] = [];
  const mice: FurnitureItem[] = [];
  const computerTowers: FurnitureItem[] = [];
  const chairSeats: FurnitureItem[] = [];
  const chairBacks: FurnitureItem[] = [];
  const chairArms: FurnitureItem[] = [];
  const chairPosts: FurnitureItem[] = [];
  const chairBases: FurnitureItem[] = [];
  const casterPositions: OfficePosition[] = [];

  for (const { stationPosition: [x, , z], chairPosition: [, , chairZ] } of agents) {
    desktops.push(item(x, 0.79, z));
    for (const dx of [-0.73, 0.73]) {
      for (const dz of [-0.35, 0.35]) deskLegs.push(item(x + dx, 0.39, z + dz));
    }
    monitorFrames.push(item(x, 1.21, z - 0.32));
    monitorStands.push(item(x, 0.91, z - 0.32));
    monitorScreens.push(item(x, 1.21, z - 0.252));
    keyboards.push(item(x, 0.88, z + 0.18));
    mice.push(item(x + 0.48, 0.88, z + 0.18));
    computerTowers.push(item(x - 0.58, 0.39, z - 0.18));
    chairSeats.push(item(x, 0.61, chairZ));
    chairBacks.push(item(x, 1, chairZ + 0.33));
    chairPosts.push(item(x, 0.3, chairZ));
    chairBases.push(item(x, 0.1, chairZ), { position: [x, 0.1, chairZ], rotation: [0, Math.PI / 2, 0] });
    chairArms.push(item(x - 0.44, 0.83, chairZ), item(x + 0.44, 0.83, chairZ));
    for (const [dx, dz] of [[-0.32, -0.28], [0.32, -0.28], [-0.32, 0.28], [0.32, 0.28]] as const) {
      casterPositions.push([x + dx, 0.08, chairZ + dz]);
    }
  }

  return (
    <group>
      <InstancedBoxes items={desktops} dimensions={[1.85, 0.12, 1.05]} color="#987052" />
      <InstancedBoxes items={deskLegs} dimensions={[0.09, 0.78, 0.09]} color="#70543c" />
      <InstancedBoxes items={monitorFrames} dimensions={[0.95, 0.63, 0.12]} color="#444b48" />
      <InstancedBoxes items={monitorStands} dimensions={[0.14, 0.22, 0.14]} color="#444b48" />
      <InstancedBoxes items={monitorScreens} dimensions={[0.79, 0.47, 0.02]} color="#9bb9b4" unlit />
      <InstancedBoxes items={keyboards} dimensions={[0.65, 0.04, 0.24]} color="#d8d0c0" />
      <InstancedBoxes items={mice} dimensions={[0.1, 0.05, 0.15]} color="#d8d0c0" />
      <InstancedBoxes items={computerTowers} dimensions={[0.3, 0.58, 0.52]} color="#555951" />
      <InstancedBoxes items={chairSeats} dimensions={[0.72, 0.16, 0.62]} color="#77796c" />
      <InstancedBoxes items={chairBacks} dimensions={[0.7, 0.78, 0.14]} color="#77796c" />
      <InstancedBoxes items={chairArms} dimensions={[0.08, 0.06, 0.46]} color="#555951" />
      <InstancedBoxes items={chairPosts} dimensions={[0.1, 0.36, 0.1]} color="#555951" />
      <InstancedBoxes items={chairBases} dimensions={[0.1, 0.08, 0.9]} color="#555951" />
      <Instances limit={casterPositions.length} range={casterPositions.length}>
        <sphereGeometry args={[0.09, 8, 6]} />
        <meshLambertMaterial color="#555951" />
        {casterPositions.map((position, index) => <Instance key={index} position={position} />)}
      </Instances>
    </group>
  );
}

function OfficePlant({ position }: { position: [number, number, number] }) {
  return (
    <group position={position}>
      <mesh position={[0, 0.25, 0]}><cylinderGeometry args={[0.22, 0.26, 0.48, 7]} /><meshLambertMaterial color="#ba8058" /></mesh>
      <mesh position={[0, 0.82, 0]}><coneGeometry args={[0.48, 1.12, 6]} /><meshLambertMaterial color="#58775c" /></mesh>
      <mesh position={[0.28, 0.73, 0.16]} rotation={[0, 0, -0.32]}><coneGeometry args={[0.27, 0.68, 5]} /><meshLambertMaterial color="#78936a" /></mesh>
    </group>
  );
}

function OfficeRestRoom({ layout, agents }: { layout: OfficeLayout; agents: OfficeSceneAgent[] }) {
  const width = layout.roomWidth;
  const depth = layout.restRoomDepth;
  const centerZ = layout.restRoomCenter[2];
  const frontZ = layout.restRoomFrontZ;
  const doorX = layout.restRoomDoorX;
  const wallHeight = 2.25;
  const doorWidth = 2.4;
  const doorHeight = 2.05;
  const leftWidth = doorX - doorWidth / 2 + width / 2;
  const rightWidth = width / 2 - doorX - doorWidth / 2;
  const leftCenterX = -width / 2 + leftWidth / 2;
  const rightCenterX = doorX + doorWidth / 2 + rightWidth / 2;
  const seats: FurnitureItem[] = [];
  const backs: FurnitureItem[] = [];
  const arms: FurnitureItem[] = [];
  const legs: FurnitureItem[] = [];

  for (const agent of agents) {
    const [x, , z] = agent.restSeatPosition;
    seats.push({ position: [x, 0.5, z] });
    backs.push({ position: [x, 0.88, z - 0.3] });
    arms.push({ position: [x - 0.38, 0.73, z + 0.02] }, { position: [x + 0.38, 0.73, z + 0.02] });
    for (const dx of [-0.29, 0.29]) {
      for (const dz of [-0.2, 0.2]) legs.push({ position: [x + dx, 0.24, z + dz] });
    }
  }

  return (
    <group>
      <mesh position={[0, 0.025, centerZ]} rotation={[-Math.PI / 2, 0, 0]} receiveShadow>
        <planeGeometry args={[width - 0.5, depth - 0.4]} />
        <meshLambertMaterial color="#c9c4b2" />
      </mesh>
      <mesh position={[-width / 2 + 0.18, wallHeight / 2, centerZ]} receiveShadow>
        <boxGeometry args={[0.16, wallHeight, depth]} />
        <meshLambertMaterial color="#b9b09f" />
      </mesh>
      <mesh position={[width / 2 - 0.18, wallHeight / 2, centerZ]} receiveShadow>
        <boxGeometry args={[0.16, wallHeight, depth]} />
        <meshLambertMaterial color="#b9b09f" />
      </mesh>
      <mesh position={[leftCenterX, wallHeight / 2, frontZ]} receiveShadow>
        <boxGeometry args={[leftWidth, wallHeight, 0.18]} />
        <meshLambertMaterial color="#b9b09f" />
      </mesh>
      <mesh position={[rightCenterX, wallHeight / 2, frontZ]} receiveShadow>
        <boxGeometry args={[rightWidth, wallHeight, 0.18]} />
        <meshLambertMaterial color="#b9b09f" />
      </mesh>
      <mesh position={[doorX, doorHeight + (wallHeight - doorHeight) / 2, frontZ]} receiveShadow>
        <boxGeometry args={[doorWidth, wallHeight - doorHeight, 0.18]} />
        <meshLambertMaterial color="#b9b09f" />
      </mesh>
      <mesh position={[doorX - doorWidth / 2, doorHeight / 2, frontZ + 0.04]}>
        <boxGeometry args={[0.1, doorHeight, 0.12]} />
        <meshLambertMaterial color="#8c765e" />
      </mesh>
      <mesh position={[doorX + doorWidth / 2, doorHeight / 2, frontZ + 0.04]}>
        <boxGeometry args={[0.1, doorHeight, 0.12]} />
        <meshLambertMaterial color="#8c765e" />
      </mesh>
      <mesh position={[doorX, doorHeight, frontZ + 0.04]}>
        <boxGeometry args={[doorWidth + 0.1, 0.1, 0.12]} />
        <meshLambertMaterial color="#8c765e" />
      </mesh>
      <InstancedBoxes items={legs} dimensions={[0.07, 0.48, 0.07]} color="#5d6158" />
      <InstancedBoxes items={seats} dimensions={[0.76, 0.15, 0.68]} color="#74816e" />
      <InstancedBoxes items={backs} dimensions={[0.76, 0.76, 0.13]} color="#65745f" />
      <InstancedBoxes items={arms} dimensions={[0.1, 0.5, 0.62]} color="#65745f" />
      <Html position={[doorX, wallHeight + 0.12, frontZ + 0.02]} center distanceFactor={15} style={{ pointerEvents: "none" }}>
        <span style={{ padding: "5px 10px", borderRadius: 8, color: "#f7f3e9", background: "#40544a", font: "600 11px/1.2 system-ui", letterSpacing: "0.12em", whiteSpace: "nowrap" }}>
          REST ROOM
        </span>
      </Html>
    </group>
  );
}


function OfficeSetDressing({ width, depth }: { width: number; depth: number }) {
  const side = width / 2 - 0.12;
  const rear = -depth / 2 + 0.12;
  const front = depth / 2 - 0.7;
  const wallHeight = 4.2;
  const doorWidth = 2;
  const doorHeight = 2.65;
  const sideWallWidth = (width - doorWidth) / 2;
  const doorFront = rear + 0.17;
  return (
    <group>
      <mesh position={[-doorWidth / 2 - sideWallWidth / 2, wallHeight / 2, rear]} receiveShadow><boxGeometry args={[sideWallWidth, wallHeight, 0.24]} /><meshLambertMaterial color="#c7bba6" /></mesh>
      <mesh position={[doorWidth / 2 + sideWallWidth / 2, wallHeight / 2, rear]} receiveShadow><boxGeometry args={[sideWallWidth, wallHeight, 0.24]} /><meshLambertMaterial color="#c7bba6" /></mesh>
      <mesh position={[0, (wallHeight + doorHeight) / 2, rear]} receiveShadow><boxGeometry args={[doorWidth, wallHeight - doorHeight, 0.24]} /><meshLambertMaterial color="#c7bba6" /></mesh>
      <mesh position={[-side, 0.58, 0]} receiveShadow><boxGeometry args={[0.24, 1.16, depth]} /><meshLambertMaterial color="#c7bba6" /></mesh>
      <mesh position={[side, 0.58, 0]} receiveShadow><boxGeometry args={[0.24, 1.16, depth]} /><meshLambertMaterial color="#c7bba6" /></mesh>
      <mesh position={[-width * 0.23, 2.45, rear + 0.13]}><boxGeometry args={[width * 0.29, 1.25, 0.03]} /><meshLambertMaterial color="#b4c9c5" /></mesh>
      <mesh position={[width * 0.23, 2.45, rear + 0.13]}><boxGeometry args={[width * 0.29, 1.25, 0.03]} /><meshLambertMaterial color="#b4c9c5" /></mesh>
      <mesh position={[0, doorHeight / 2, doorFront]} castShadow><boxGeometry args={[doorWidth - 0.16, doorHeight - 0.08, 0.12]} /><meshLambertMaterial color="#80654a" /></mesh>
      <mesh position={[-doorWidth / 2 + 0.04, doorHeight / 2, doorFront + 0.07]}><boxGeometry args={[0.12, doorHeight + 0.08, 0.16]} /><meshLambertMaterial color="#b69a75" /></mesh>
      <mesh position={[doorWidth / 2 - 0.04, doorHeight / 2, doorFront + 0.07]}><boxGeometry args={[0.12, doorHeight + 0.08, 0.16]} /><meshLambertMaterial color="#b69a75" /></mesh>
      <mesh position={[0, doorHeight + 0.04, doorFront + 0.07]}><boxGeometry args={[doorWidth + 0.12, 0.14, 0.16]} /><meshLambertMaterial color="#b69a75" /></mesh>
      <mesh position={[0.63, 1.24, doorFront + 0.15]}><sphereGeometry args={[0.08, 10, 8]} /><meshLambertMaterial color="#c29b5b" /></mesh>
      <OfficePlant position={[-side + 0.75, 0, rear + 0.9]} />
      <OfficePlant position={[side - 0.75, 0, rear + 0.9]} />
      <OfficePlant position={[-side + 0.75, 0, front]} />
      <OfficePlant position={[side - 0.75, 0, front]} />
    </group>
  );
}

function OfficeSharedFurniture({ width, centerZ }: { width: number; centerZ: number }) {
  const tableX = -width * 0.25;
  const boardX = -width * 0.24;
  const boardZ = centerZ + 2.15;
  const tableLegs: FurnitureItem[] = [];
  const seats: FurnitureItem[] = [];
  const backs: FurnitureItem[] = [];
  const chairLegs: FurnitureItem[] = [];

  for (const side of [-1, 1]) {
    for (const offsetX of [-0.95, 0, 0.95]) {
      const x = tableX + offsetX;
      const z = centerZ + side * 1.16;
      seats.push({ position: [x, 0.47, z] });
      backs.push({ position: [x, 0.82, centerZ + side * 1.48] });
      for (const dx of [-0.22, 0.22]) {
        for (const dz of [-0.2, 0.2]) chairLegs.push({ position: [x + dx, 0.23, z + dz] });
      }
    }
  }

  for (const dx of [-1.25, 1.25]) {
    for (const dz of [-0.45, 0.45]) tableLegs.push({ position: [tableX + dx, 0.4, centerZ + dz] });
  }

  return (
    <group>
      <mesh position={[0, 0.022, centerZ]} rotation={[-Math.PI / 2, 0, 0]} receiveShadow>
        <planeGeometry args={[width - 2, 4.35]} />
        <meshLambertMaterial color="#d9d1c1" />
      </mesh>
      <mesh position={[boardX, 1.55, boardZ]} castShadow>
        <boxGeometry args={[2.45, 1.42, 0.08]} />
        <meshLambertMaterial color="#f0ece0" />
      </mesh>
      <mesh position={[boardX - 1.27, 1.55, boardZ + 0.06]}><boxGeometry args={[0.08, 1.56, 0.12]} /><meshLambertMaterial color="#80654a" /></mesh>
      <mesh position={[boardX + 1.27, 1.55, boardZ + 0.06]}><boxGeometry args={[0.08, 1.56, 0.12]} /><meshLambertMaterial color="#80654a" /></mesh>
      <mesh position={[boardX, 2.29, boardZ + 0.06]}><boxGeometry args={[2.62, 0.08, 0.12]} /><meshLambertMaterial color="#80654a" /></mesh>
      <mesh position={[boardX, 0.81, boardZ + 0.06]}><boxGeometry args={[2.62, 0.08, 0.12]} /><meshLambertMaterial color="#80654a" /></mesh>
      <mesh position={[boardX, 0.72, boardZ + 0.14]}><boxGeometry args={[1.1, 0.06, 0.18]} /><meshLambertMaterial color="#987052" /></mesh>
      <InstancedBoxes items={tableLegs} dimensions={[0.12, 0.8, 0.12]} color="#70543c" />
      <mesh position={[tableX, 0.84, centerZ]} castShadow receiveShadow>
        <boxGeometry args={[2.9, 0.12, 1.15]} />
        <meshLambertMaterial color="#987052" />
      </mesh>
      <InstancedBoxes items={chairLegs} dimensions={[0.07, 0.44, 0.07]} color="#625848" />
      <InstancedBoxes items={seats} dimensions={[0.62, 0.12, 0.58]} color="#77796c" />
      <InstancedBoxes items={backs} dimensions={[0.62, 0.62, 0.12]} color="#77796c" />
    </group>
  );
}


function SceneContents({ agents, zoom, focusTarget, reducedMotion, simplified, layout, onSelectAgent }: OfficeSceneProps & { layout: OfficeLayout }) {
  const { size } = useThree();
  const floorWidth = layout.roomWidth;
  const floorDepth = layout.roomDepth;
  const aspect = Math.max(0.1, size.width / Math.max(1, size.height));
  const verticalHalfFov = (CAMERA_FOV * Math.PI) / 360;
  const horizontalHalfFov = Math.atan(Math.tan(verticalHalfFov) * aspect);
  const fitDistance = Math.max(
    floorWidth / (2 * Math.tan(horizontalHalfFov)),
    floorDepth / (2 * Math.tan(verticalHalfFov)),
  ) * 1.2;
  const cameraZoom = Math.max(7, fitDistance + zoom - 15);
  const fullAvatars = agents.filter((agent) => agent.fullAvatar);
  const staticAgents = agents.filter((agent) => !agent.fullAvatar);
  const selectedStaticAgent = staticAgents.find((agent) => agent.selected);

  return (
    <>
      <color attach="background" args={["#eee8dc"]} />
      <ambientLight intensity={1.45} />
      <hemisphereLight args={["#fffaf0", "#8d927f", 0.7]} />
      <directionalLight position={[-6, 14, 8]} intensity={2.1} castShadow={!simplified} shadow-mapSize-width={1024} shadow-mapSize-height={1024} />
      <mesh position={[0, -0.1, 0]} receiveShadow>
        <boxGeometry args={[floorWidth, 0.2, floorDepth]} />
        <meshLambertMaterial color="#e7dfd0" />
      </mesh>
      <OfficeSetDressing width={floorWidth} depth={floorDepth} />
      <OfficeWorkstations agents={agents} />
      <OfficeRestRoom layout={layout} agents={agents} />
      <OfficeSharedFurniture width={floorWidth} centerZ={layout.sharedAreaCenter[2]} />
      <mesh position={[0, 0.015, 0]} rotation={[-Math.PI / 2, 0, 0]}>
        <planeGeometry args={[floorWidth, floorDepth]} />
        <meshBasicMaterial transparent opacity={0} depthWrite={false} />
      </mesh>
      <StaticAgentMarkers agents={staticAgents} onSelectAgent={onSelectAgent} />
      {selectedStaticAgent ? <SelectedMarkerLabel agent={selectedStaticAgent} /> : null}
      <Suspense fallback={null}>
        {fullAvatars.map((agent) => (
          <AnimatedAgent key={agent.id} agent={agent} reducedMotion={reducedMotion} onSelectAgent={onSelectAgent} />
        ))}
      </Suspense>
      <CameraRig zoom={cameraZoom} focusTarget={focusTarget} />
      <OrbitControls
        makeDefault
        enablePan
        enableRotate
        enableZoom
        minDistance={7}
        maxDistance={Math.max(42, fitDistance + 20)}
        maxPolarAngle={Math.PI / 2.12}
        target={focusTarget}
      />
    </>
  );
}

export function OfficeScene(props: OfficeSceneProps) {
  const layout = createOfficeLayout(props.agents.length);
  return (
    <Canvas
      camera={{
        position: [7, 14, 7],
        fov: CAMERA_FOV,
        near: 0.1,
        far: Math.max(250, Math.hypot(layout.roomWidth, layout.roomDepth) * 4),
      }}
      dpr={props.simplified ? [1, 1.1] : [1, 1.5]}
      gl={{ antialias: false, alpha: false, powerPreference: "low-power" }}
      shadows={!props.simplified}
      tabIndex={0}
      aria-label="Three-dimensional view of persisted Agent Instances. Positions are visual presentation anchors, not saved workspace locations."
      onPointerDown={(event) => event.currentTarget.focus()}
    >
      <SceneContents {...props} layout={layout} />
    </Canvas>
  );
}

