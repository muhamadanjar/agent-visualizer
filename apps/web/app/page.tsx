"use client";

import { useCallback, useEffect, useState, type FormEvent } from "react";
import { useRouter } from "next/navigation";

type Owner = { username: string };

type AuthState =
  | { status: "loading" }
  | { status: "signed-out" }
  | { status: "signed-in"; owner: Owner }
  | { status: "error"; message: string };

const ME_ENDPOINT = "/api/v1/auth/me";
const LOGIN_ENDPOINT = "/api/v1/auth/login";

function parseOwner(value: unknown): Owner | null {
  if (typeof value !== "object" || value === null || !("username" in value)) return null;
  const { username } = value;
  return typeof username === "string" && username.length > 0 ? { username } : null;
}
async function requestOwner(): Promise<Owner | null> {
  const response = await fetch(ME_ENDPOINT, {
    cache: "no-store",
    credentials: "same-origin",
    headers: { accept: "application/json" },
  });

  if (response.status === 401) return null;
  if (!response.ok) throw new Error("session check failed");

  const body: unknown = await response.json();
  const owner = parseOwner(body);
  if (!owner) throw new Error("unexpected session response");
  return owner;
}

export default function HomePage() {
  const router = useRouter();
  const [auth, setAuth] = useState<AuthState>({ status: "loading" });
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);
  const loadSession = useCallback(async () => {
    setAuth({ status: "loading" });
    try {
      const owner = await requestOwner();
      setAuth(owner ? { status: "signed-in", owner } : { status: "signed-out" });
    } catch {
      setAuth({ status: "error", message: "Tidak dapat memverifikasi sesi pemilik." });
    }
  }, []);

  useEffect(() => {
    void loadSession();
  }, [loadSession]);
  useEffect(() => {
    if (auth.status === "signed-in") router.replace("/office");
  }, [auth.status, router]);

  async function handleLogin(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (pending) return;

    const submittedUsername = username.trim();
    const submittedPassword = password;
    setPassword("");
    setError(null);
    setPending(true);

    try {
      const response = await fetch(LOGIN_ENDPOINT, {
        method: "POST",
        credentials: "same-origin",
        headers: { "content-type": "application/json", accept: "application/json" },
        body: JSON.stringify({ username: submittedUsername, password: submittedPassword }),
      });

      if (!response.ok) {
        setError("Gagal masuk. Periksa nama pengguna dan kata sandi.");
        return;
      }

      const owner = await requestOwner();
      if (!owner) {
        setError("Gagal masuk. Periksa nama pengguna dan kata sandi.");
        return;
      }

      setUsername("");
      setAuth({ status: "signed-in", owner });
    } catch {
      setError("Gagal masuk. Server tidak dapat dijangkau.");
    } finally {
      setPending(false);
    }
  }


  return (
    <main className="shell">
      <header className="masthead">
        <p className="eyebrow">Pilot privat</p>
        <h1>Agent Office</h1>
        <p className="lede">Akses hanya untuk pemilik. Pendaftaran publik tidak tersedia.</p>
      </header>

      <section className="panel" aria-labelledby="session-heading">
        <h2 id="session-heading">Sesi pemilik</h2>

        {auth.status === "loading" ? (
          <p role="status">Memeriksa sesi…</p>
        ) : null}

        {auth.status === "error" ? (
          <div className="stack">
            <p role="alert">{auth.message}</p>
            <button className="button" type="button" onClick={() => void loadSession()}>
              Coba lagi
            </button>
          </div>
        ) : null}

        {auth.status === "signed-in" ? (
          <p role="status">Sesi terverifikasi. Membuka workspace…</p>
        ) : null}

        {auth.status === "signed-out" ? (
          <form className="stack" onSubmit={handleLogin}>
            <div className="field">
              <label htmlFor="username">Nama pengguna</label>
              <input
                id="username"
                name="username"
                type="text"
                autoComplete="username"
                autoCapitalize="none"
                spellCheck={false}
                maxLength={128}
                required
                value={username}
                onChange={(event) => setUsername(event.target.value)}
                disabled={pending}
              />
            </div>

            <div className="field">
              <label htmlFor="password">Kata sandi</label>
              <input
                id="password"
                name="password"
                type="password"
                autoComplete="current-password"
                maxLength={1024}
                required
                value={password}
                onChange={(event) => setPassword(event.target.value)}
                disabled={pending}
              />
            </div>

            <button className="button" type="submit" disabled={pending || username.trim() === "" || password === ""}>
              {pending ? "Sedang masuk…" : "Masuk"}
            </button>
          </form>
        ) : null}

        {error ? (
          <p className="error" role="alert">
            {error}
          </p>
        ) : null}
      </section>
    </main>
  );
}
