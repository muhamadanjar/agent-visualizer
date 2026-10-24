"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useCallback, useEffect, useState, type ReactNode } from "react";

type Owner = { username: string };
type SessionState = "checking" | "ready" | "error";
const navigation = [

  { href: "/office", label: "Office", hint: "O" },
  { href: "/projects", label: "Projects", hint: "P" },
  { href: "/agents", label: "Agents", hint: "A" },
  { href: "/agents/definitions", label: "Definitions", hint: "D" },
  { href: "/skills", label: "Skills", hint: "K" },
  { href: "/tasks", label: "Tasks", hint: "T" },
  { href: "/runs/run-sample-research", label: "Run detail", hint: "R" },
  { href: "/inbox", label: "Inbox", hint: "I" },
  { href: "/delegations", label: "Delegations", hint: "G" },
  { href: "/connections", label: "Connections", hint: "C" },
  { href: "/usage", label: "Usage", hint: "U" },
  { href: "/audit", label: "Audit", hint: "H" },
  { href: "/readiness", label: "Readiness", hint: "M" },
  { href: "/settings", label: "Settings", hint: "S" },
];
function isNavigationActive(pathname: string, href: string) {
  if (href === "/runs/run-sample-research") return pathname.startsWith("/runs/");
  if (href === "/agents") return pathname === href || (pathname.startsWith("/agents/") && !pathname.startsWith("/agents/definitions"));
  return pathname === href || pathname.startsWith(`${href}/`);
}

async function getOwner(signal?: AbortSignal): Promise<Owner | null> {
  const response = await fetch("/api/v1/auth/me", {
    cache: "no-store",
    credentials: "same-origin",
    headers: { accept: "application/json" },
    signal,
  });
  if (response.status === 401) return null;
  if (!response.ok) throw new Error("Session check failed");
  const body: unknown = await response.json();
  if (typeof body !== "object" || body === null || !("username" in body) || typeof body.username !== "string") {
    throw new Error("Unexpected session response");
  }
  return { username: body.username };
}

export function WorkspaceShell({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  const [sessionState, setSessionState] = useState<SessionState>("checking");
  const [owner, setOwner] = useState<Owner | null>(null);
  const [logoutError, setLogoutError] = useState<string | null>(null);
  const [loggingOut, setLoggingOut] = useState(false);

  const checkSession = useCallback(async (signal?: AbortSignal) => {
    setSessionState("checking");
    try {
      const nextOwner = await getOwner(signal);
      if (signal?.aborted) return;
      if (!nextOwner) {
        router.replace("/");
        return;
      }
      setOwner(nextOwner);
      setSessionState("ready");
    } catch {
      if (!signal?.aborted) setSessionState("error");
    }
  }, [router]);

  useEffect(() => {
    const controller = new AbortController();
    void checkSession(controller.signal);
    return () => controller.abort();
  }, [checkSession]);

  async function handleLogout() {
    if (loggingOut) return;
    setLoggingOut(true);
    setLogoutError(null);
    try {
      const response = await fetch("/api/v1/auth/logout", {
        method: "POST",
        credentials: "same-origin",
        headers: { accept: "application/json" },
      });
      if (!response.ok) throw new Error("Logout failed");
      router.replace("/");
    } catch {
      setLogoutError("Tidak dapat keluar. Periksa koneksi, lalu coba lagi.");
      setLoggingOut(false);
    }
  }

  if (sessionState === "checking") {
    return <main className="workspace-gate" role="status">Memeriksa sesi pemilik…</main>;
  }
  if (sessionState === "error") {
    return (
      <main className="workspace-gate">
        <section className="gate-card" aria-labelledby="session-error-heading">
          <p className="eyebrow">Agent Office</p>
          <h1 id="session-error-heading">Sesi tidak dapat diverifikasi</h1>
          <p className="muted">Pastikan server tersedia sebelum membuka workspace.</p>
          <button className="button" type="button" onClick={() => void checkSession()}>Coba lagi</button>
          <Link className="text-link" href="/">Kembali ke halaman masuk</Link>
        </section>
      </main>
    );
  }
  if (!owner) return <main className="workspace-gate" role="status">Mengarahkan ke halaman masuk…</main>;
  const isProjectPath = pathname === "/projects" || pathname.startsWith("/projects/");
  const isTaskPath = pathname === "/tasks" || pathname.startsWith("/tasks/");
  const isApiDataPath = isProjectPath || isTaskPath;
  const dataLabel = isProjectPath ? "Projects · API data" : isTaskPath ? "Tasks · API data" : "Sample data · UI preview";

  return (
    <div className="workspace-frame">
      <a className="skip-link" href="#workspace-content">Skip to main content</a>
      <aside className="sidebar" aria-label="Navigasi utama">
        <Link className="brand-lockup" href="/office" aria-label="Agent Office, buka office">
          <span className="brand-mark" aria-hidden="true">AO</span>
          <span><strong>Agent Office</strong><small>Personal workspace</small></span>
        </Link>
        <div className="sidebar-section-label">Workspace</div>
        <nav className="primary-nav">
          {navigation.map((item) => {
            const active = isNavigationActive(pathname, item.href);
            return (
              <Link key={item.href} className={`nav-link${active ? " is-active" : ""}`} href={item.href} aria-current={active ? "page" : undefined}>
                <span className="nav-glyph" aria-hidden="true">{item.hint}</span>
                <span>{item.label}</span>
              </Link>
            );
          })}
        </nav>
        <div className="sidebar-bottom">
          <div className="sample-legend"><span className={isApiDataPath ? "context-indicator" : "sample-dot"} />{dataLabel}</div>
          <div className="owner-card">
            <span className="owner-avatar" aria-hidden="true">{owner.username.slice(0, 1).toUpperCase()}</span>
            <span className="owner-name"><strong>{owner.username}</strong><small>Owner</small></span>
            <button className="quiet-button logout-button" type="button" onClick={() => void handleLogout()} disabled={loggingOut} aria-label="Keluar">
              {loggingOut ? "…" : "Keluar"}
            </button>
          </div>
          {logoutError ? <p className="inline-error" role="alert">{logoutError}</p> : null}
        </div>
      </aside>

      <div className="workspace-main">
        <header className="workspace-topbar">
          <div className="mobile-brand"><span className="brand-mark" aria-hidden="true">AO</span><strong>Agent Office</strong></div>
          <div className="topbar-context"><span className="context-indicator" />Private workspace</div>
          <div className="topbar-right"><span className="sample-badge">{isApiDataPath ? "API-backed records" : "Sample data"}</span><span className="topbar-owner">{owner.username}</span></div>
        </header>
        <nav className="mobile-nav" aria-label="Workspace navigation">
          {navigation.filter((item) => item.label !== "Settings").map((item) => {
            const active = isNavigationActive(pathname, item.href);
            return <Link key={item.href} className={active ? "mobile-nav-link is-active" : "mobile-nav-link"} href={item.href} aria-current={active ? "page" : undefined}>{item.label}</Link>;
          })}
          <Link className="mobile-nav-link" href="/settings">Settings</Link>
        </nav>
        <main className="workspace-content" id="workspace-content" tabIndex={-1}>{children}</main>
      </div>
    </div>
  );
}
