# M0 3D feasibility spike

This isolated scene checks the recommended React Three Fiber/Rapier path for movement, collision, click-to-move routing, camera follow/overview, and a local interaction target. It is a technical spike, not the Agent Office application. It has no runtime connection, agent telemetry, backend, or model calls.

## Requirements

- Bun 1.3.14 or newer.
- Node.js 20.9 or newer (required by Next.js).
- A browser with WebGL 2 support. The M0 browser smoke uses the managed Chromium browser supplied by the development environment.

## Run

```sh
bun install --frozen-lockfile
bun run dev
```

Open the address printed by Next.js. Use WASD/arrow keys to walk; click the floor to route; `E` or click the blue console marker to interact; use the camera button to switch between follow and overview.

## Pinned stack

The package manifest pins Next.js 16.4.2, React/React DOM 19.3.1, React Three Fiber 9.8.2, Drei 10.7.9, and React Three Rapier 2.2.0. Registry peer metadata confirmed Fiber 9 uses React 19 and Rapier 2 supports Fiber 9/React 19. This pin set is for the spike only; it is not yet the production lockfile decision.

## Known limits

- One simple floor, fixed blockers, and a small grid; no layout editor or multiple floors.
- The pathfinder is an M0 grid/A* proof, not a general navmesh.
- A displayed FPS estimate and keydown-to-first-movement-frame sample are diagnostic observations, not a performance acceptance result.
- Browser movement is local presentation only. The interaction marker does not send a task or communicate with an agent.
