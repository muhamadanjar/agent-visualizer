import { constants } from "node:fs";
import { chmod, mkdir, open, readdir } from "node:fs/promises";
import { basename, dirname, isAbsolute, join, relative, sep } from "node:path";
import { isRecord } from "./runtime-types";

export const OMP_PACKAGE_NAME = "@oh-my-pi/pi-coding-agent";
export const OMP_PACKAGE_VERSION = "18.12.3";
const DIRECTORY_FLAGS = constants.O_RDONLY | constants.O_DIRECTORY | constants.O_NOFOLLOW;

type OpenHandle = Awaited<ReturnType<typeof open>>;


export function isWithin(root: string, target: string): boolean {
  const pathFromRoot = relative(root, target);
  return pathFromRoot === "" ||
    (pathFromRoot !== ".." && !pathFromRoot.startsWith(`..${sep}`) && !isAbsolute(pathFromRoot));
}

export function pathsOverlap(first: string, second: string): boolean {
  return isWithin(first, second) || isWithin(second, first);
}

function descriptorPath(directory: OpenHandle): string {
  return `/proc/self/fd/${directory.fd}`;
}

export async function openAbsoluteDirectory(directory: string): Promise<OpenHandle> {
  if (!isAbsolute(directory)) throw new Error("omp_runtime_directory_not_absolute");
  let current: OpenHandle | null = await open(sep, DIRECTORY_FLAGS);
  try {
    for (const segment of directory.split(sep).filter(Boolean)) {
      if (!current) throw new Error("omp_runtime_directory_handle_missing");
      const next = await open(join(descriptorPath(current), segment), DIRECTORY_FLAGS);
      const previous = current;
      current = next;
      await previous.close();
    }
    if (!current) throw new Error("omp_runtime_directory_handle_missing");
    const result = current;
    current = null;
    return result;
  } finally {
    if (current) await current.close();
  }
}

async function openPackageDirectory(root: OpenHandle, packageName: string): Promise<OpenHandle> {
  let parent = root;
  let ownedParent: OpenHandle | null = null;
  try {
    for (const segment of packageName.split("/")) {
      if (!segment || segment === "." || segment === ".." || segment.includes("\\")) {
        throw new Error("omp_runtime_dependency_name_invalid");
      }
      const child = await open(join(descriptorPath(parent), segment), DIRECTORY_FLAGS);
      const previous = ownedParent;
      ownedParent = child;
      parent = child;
      if (previous) await previous.close();
    }
    if (!ownedParent) throw new Error("omp_runtime_dependency_name_invalid");
    const packageDirectory = ownedParent;
    ownedParent = null;
    return packageDirectory;
  } finally {
    if (ownedParent) await ownedParent.close();
  }
}

async function copyFileContents(
  source: OpenHandle,
  destination: string,
  buffer: Uint8Array,
): Promise<void> {
  const target = await open(
    destination,
    constants.O_WRONLY | constants.O_CREAT | constants.O_EXCL | constants.O_NOFOLLOW,
    0o600,
  );
  try {
    while (true) {
      const { bytesRead } = await source.read(buffer, 0, buffer.byteLength, null);
      if (bytesRead === 0) return;
      let written = 0;
      while (written < bytesRead) {
        const { bytesWritten } = await target.write(buffer, written, bytesRead - written, null);
        if (bytesWritten === 0) throw new Error("omp_runtime_copy_write_stalled");
        written += bytesWritten;
      }
    }
  } finally {
    await target.close();
  }
}

export async function copyRunningFile(
  sourcePath: string,
  destination: string,
  buffer: Uint8Array,
  mode = 0o500,
): Promise<void> {
  const sourceDirectory = await openAbsoluteDirectory(dirname(sourcePath));
  try {
    const source = await open(
      join(descriptorPath(sourceDirectory), basename(sourcePath)),
      constants.O_RDONLY | constants.O_NOFOLLOW | constants.O_NONBLOCK,
    );
    try {
      if (!(await source.stat()).isFile()) throw new Error("omp_runtime_executable_not_regular_file");
      await copyFileContents(source, destination, buffer);
    } finally {
      await source.close();
    }
  } finally {
    await sourceDirectory.close();
  }
  await chmod(destination, mode);
}

async function copyDirectoryContents(
  source: OpenHandle,
  destination: string,
  buffer: Uint8Array,
): Promise<void> {
  await mkdir(destination, { mode: 0o700 });
  const entries = await readdir(descriptorPath(source), { withFileTypes: true });
  for (const entry of entries) {
    if (entry.name === ".git") continue;
    if (entry.isSymbolicLink()) throw new Error("omp_runtime_package_symlink_unsupported");
    if (!entry.isDirectory() && !entry.isFile()) throw new Error("omp_runtime_entry_type_unsupported");
    const sourcePath = join(descriptorPath(source), entry.name);
    const flags = constants.O_RDONLY | constants.O_NOFOLLOW |
      (entry.isDirectory() ? constants.O_DIRECTORY : constants.O_NONBLOCK);
    const opened = await open(sourcePath, flags);
    try {
      const stats = await opened.stat();
      const destinationPath = join(destination, entry.name);
      if (stats.isDirectory()) {
        await copyDirectoryContents(opened, destinationPath, buffer);
      } else if (stats.isFile()) {
        await copyFileContents(opened, destinationPath, buffer);
      } else {
        throw new Error("omp_runtime_entry_type_unsupported");
      }
    } finally {
      await opened.close();
    }
  }
}

export async function stageRuntimePackages(
  packageRoot: OpenHandle,
  stagedRoot: string,
  buffer: Uint8Array,
): Promise<void> {
  const pending = [OMP_PACKAGE_NAME];
  const staged = new Set<string>();

  while (pending.length > 0) {
    const packageName = pending.pop();
    if (!packageName || staged.has(packageName)) continue;
    const source = await openPackageDirectory(packageRoot, packageName);
    try {
      const manifestFile = await open(
        join(descriptorPath(source), "package.json"),
        constants.O_RDONLY | constants.O_NOFOLLOW | constants.O_NONBLOCK,
      );
      let manifestText: string;
      try {
        if (!(await manifestFile.stat()).isFile()) {
          throw new Error("omp_runtime_package_manifest_not_regular");
        }
        manifestText = await manifestFile.readFile("utf8");
      } finally {
        await manifestFile.close();
      }
      const manifestValue: unknown = JSON.parse(manifestText);
      if (
        !isRecord(manifestValue) ||
        manifestValue.name !== packageName ||
        (packageName === OMP_PACKAGE_NAME && manifestValue.version !== OMP_PACKAGE_VERSION)
      ) {
        throw new Error("omp_runtime_package_manifest_invalid");
      }
      const dependencies = isRecord(manifestValue.dependencies) ? Object.keys(manifestValue.dependencies) : [];
      const platformSuffix = `-${process.platform}-${process.arch}`;
      const platformOptionalDependencies = isRecord(manifestValue.optionalDependencies)
        ? Object.keys(manifestValue.optionalDependencies).filter((name) => name.endsWith(platformSuffix))
        : [];
      pending.push(...dependencies, ...platformOptionalDependencies);

      const destination = join(stagedRoot, packageName);
      await mkdir(dirname(destination), { recursive: true });
      await copyDirectoryContents(source, destination, buffer);
      staged.add(packageName);
    } finally {
      await source.close();
    }
  }
}

export async function withTimeout<T>(promise: Promise<T>, label: string, timeoutMs: number): Promise<T> {
  let timer: ReturnType<typeof setTimeout>;
  const timeout = new Promise<never>((_, reject) => {
    timer = setTimeout(() => reject(new Error(label)), timeoutMs);
  });
  try {
    return await Promise.race([promise, timeout]);
  } finally {
    clearTimeout(timer);
  }
}
