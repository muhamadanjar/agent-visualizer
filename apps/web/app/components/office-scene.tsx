"use client";

import { Html, Instance, Instances, OrbitControls, useAnimations, useGLTF } from "@react-three/drei";
import { Canvas, useThree } from "@react-three/fiber";
import { Suspense, useEffect, useMemo, useRef, useState, type CSSProperties } from "react";
import { LoopOnce, LoopRepeat, Mesh, MeshStandardMaterial } from "three";
import type { Group, Object3D } from "three";
import * as SkeletonUtils from "three/addons/utils/SkeletonUtils.js";

type AgentAnimation = "working" | "success" | "neutral";
export type OfficeSceneAgent = {
  id: string;
  displayName: string;
  statusLabel: string;
  animation: AgentAnimation;
  fullAvatar: boolean;
  selected: boolean;
  accent: string;
  position: [number, number, number];
};

type OfficeSceneProps = {
  agents: OfficeSceneAgent[];
  zoom: number;
  focusTarget: [number, number, number];
  reducedMotion: boolean;
  simplified: boolean;
  onSelectAgent: (agentId: string) => void;
};

const BUSINESS_MAN_MODEL = "/assets/agents/business-man.glb";
const WORKING_CLIP = "CharacterArmature|Interact";
const SUCCESS_CLIP = "CharacterArmature|Wave";

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

function AnimatedAgent({ agent, reducedMotion, onSelectAgent }: {
  agent: OfficeSceneAgent;
  reducedMotion: boolean;
  onSelectAgent: (agentId: string) => void;
}) {
  // This GLB has no Draco/Meshopt extensions; avoid initializing their WASM decoders.
  const { scene, animations } = useGLTF(BUSINESS_MAN_MODEL, false, false);
  const groupRef = useRef<Group>(null);
  const [hovered, setHovered] = useState(false);
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
  const { actions } = useAnimations(animations, groupRef);
  const clip = agent.animation === "working" ? WORKING_CLIP : agent.animation === "success" ? SUCCESS_CLIP : null;

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
    if (!clip || reducedMotion) return;
    const action = actions[clip];
    if (!action) return;
    const isSuccess = clip === SUCCESS_CLIP;
    action.reset();
    action.enabled = true;
    action.clampWhenFinished = isSuccess;
    action.setLoop(isSuccess ? LoopOnce : LoopRepeat, isSuccess ? 1 : Infinity);
    action.fadeIn(0.18).play();
    return () => { action.fadeOut(0.12); };
  }, [actions, clip, reducedMotion]);

  return (
    <group
      ref={groupRef}
      position={agent.position}
      scale={0.78}
      onClick={(event) => { event.stopPropagation(); onSelectAgent(agent.id); }}
      onPointerOver={() => setHovered(true)}
      onPointerOut={() => setHovered(false)}
    >
      <primitive object={model} />
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

function CameraRig({ zoom, focusTarget }: { zoom: number; focusTarget: [number, number, number] }) {
  const { camera } = useThree();
  useEffect(() => {
    camera.position.set(focusTarget[0] + zoom * 0.48, focusTarget[1] + zoom * 0.86, focusTarget[2] + zoom * 0.62);
    camera.up.set(0, 1, 0);
    camera.lookAt(...focusTarget);
    camera.updateProjectionMatrix();
  }, [camera, focusTarget, zoom]);
  return null;
}

function OfficeDesk({ position }: { position: [number, number, number] }) {
  return (
    <group position={position}>
      <mesh position={[0, 0.72, 0]} castShadow><boxGeometry args={[0.9, 0.1, 0.68]} /><meshLambertMaterial color="#987052" /></mesh>
      {([-1, 1] as const).flatMap((x) => ([-1, 1] as const).map((z) => (
        <mesh key={`${x}-${z}`} position={[x * 0.36, 0.35, z * 0.26]} castShadow>
          <boxGeometry args={[0.07, 0.7, 0.07]} /><meshLambertMaterial color="#70543c" />
        </mesh>
      )))}
      <mesh position={[0, 0.99, -0.2]} castShadow><boxGeometry args={[0.38, 0.34, 0.06]} /><meshLambertMaterial color="#444b48" /></mesh>
      <mesh position={[0, 1.01, -0.16]}><boxGeometry args={[0.3, 0.22, 0.02]} /><meshBasicMaterial color="#9bb9b4" /></mesh>
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

function OfficeSetDressing({ width, depth }: { width: number; depth: number }) {
  const side = width / 2 - 0.12;
  const rear = -depth / 2 + 0.12;
  const front = depth / 2 - 0.12;
  const deskX = width / 2 - 0.52;
  return (
    <group>
      <mesh position={[0, 1.15, rear]} receiveShadow><boxGeometry args={[width, 2.3, 0.24]} /><meshLambertMaterial color="#c7bba6" /></mesh>
      <mesh position={[-side, 0.95, 0]} receiveShadow><boxGeometry args={[0.24, 1.9, depth]} /><meshLambertMaterial color="#c7bba6" /></mesh>
      <mesh position={[side, 0.95, 0]} receiveShadow><boxGeometry args={[0.24, 1.9, depth]} /><meshLambertMaterial color="#c7bba6" /></mesh>
      <mesh position={[-width * 0.31, 0.65, front]}><boxGeometry args={[width * 0.38, 1.3, 0.24]} /><meshLambertMaterial color="#c7bba6" /></mesh>
      <mesh position={[width * 0.31, 0.65, front]}><boxGeometry args={[width * 0.38, 1.3, 0.24]} /><meshLambertMaterial color="#c7bba6" /></mesh>
      <mesh position={[-2.7, 1.45, rear + 0.13]}><boxGeometry args={[2.1, 0.82, 0.04]} /><meshLambertMaterial color="#b4c9c5" /></mesh>
      <mesh position={[2.7, 1.45, rear + 0.13]}><boxGeometry args={[2.1, 0.82, 0.04]} /><meshLambertMaterial color="#b4c9c5" /></mesh>
      <OfficeDesk position={[-deskX, 0, 0]} />
      <OfficeDesk position={[deskX, 0, 0]} />
      <OfficePlant position={[-side + 0.65, 0, rear + 0.82]} />
      <OfficePlant position={[side - 0.65, 0, rear + 0.82]} />
      <OfficePlant position={[-side + 0.65, 0, front - 0.82]} />
      <OfficePlant position={[side - 0.65, 0, front - 0.82]} />
    </group>
  );
}

function SceneContents({ agents, zoom, focusTarget, reducedMotion, simplified, onSelectAgent }: OfficeSceneProps) {
  const columns = 16;
  const rows = Math.max(1, Math.ceil(agents.length / columns));
  const floorWidth = columns * 0.95 + 2.5;
  const floorDepth = rows * 1.1 + 3.2;
  const fullAvatars = agents.filter((agent) => agent.fullAvatar);
  const staticAgents = agents.filter((agent) => !agent.fullAvatar);
  const selectedStaticAgent = staticAgents.find((agent) => agent.selected);
  const gridSize = Math.ceil(Math.max(floorWidth, floorDepth));

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
      <gridHelper args={[gridSize, gridSize, "#c5b9a4", "#d9d0c2"]} position={[0, 0.008, 0]} />
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
      <CameraRig zoom={zoom} focusTarget={focusTarget} />
      <OrbitControls
        makeDefault
        enablePan
        enableRotate
        enableZoom
        minDistance={7}
        maxDistance={42}
        maxPolarAngle={Math.PI / 2.12}
        target={focusTarget}
      />
    </>
  );
}

export function OfficeScene(props: OfficeSceneProps) {
  const rows = Math.max(1, Math.ceil(props.agents.length / 16));
  const baseZoom = props.agents.length === 0 ? 15 : props.agents.length === 1 ? 8 : Math.max(12, 12 + rows * 0.9);
  const fittedZoom = Math.max(7, Math.min(42, baseZoom + props.zoom - 15));
  return (
    <Canvas
      camera={{ position: [7, 14, 7], fov: 42, near: 0.1, far: 250 }}
      dpr={props.simplified ? [1, 1.1] : [1, 1.5]}
      gl={{ antialias: false, alpha: false, powerPreference: "low-power" }}
      shadows={!props.simplified}
      tabIndex={0}
      aria-label="Three-dimensional view of persisted Agent Instances. Positions are visual presentation anchors, not saved workspace locations."
      onPointerDown={(event) => event.currentTarget.focus()}
    >
      <SceneContents {...props} zoom={fittedZoom} />
    </Canvas>
  );
}

