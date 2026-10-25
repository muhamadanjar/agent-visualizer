from __future__ import annotations

from pathlib import Path

from app import config


def test_load_settings_reads_repo_dotenv_and_prefers_process_environment(
    tmp_path, monkeypatch
) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "APP_ENV=development\n"
        "OWNER_USERNAME=dotenv-owner\n"
        "OWNER_PASSWORD_HASH=$argon2id$v=19$m=65536,t=3,p=4$salt$hash\n"
        "SESSION_SECRET=dotenv-session-secret-that-is-long-enough\n"
        "PUBLIC_ORIGIN=http://localhost:8080\n"
        "DATABASE_URL=postgresql://office:password@localhost:5432/office\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(config, "ENV_FILE", env_file)
    for name in (
        "APP_ENV",
        "OWNER_USERNAME",
        "OWNER_PASSWORD_HASH",
        "SESSION_SECRET",
        "PUBLIC_ORIGIN",
        "DATABASE_URL",
    ):
        monkeypatch.delenv(name, raising=False)

    monkeypatch.setenv("OWNER_USERNAME", "process-owner")
    settings = config.load_settings()

    assert settings.owner_username == "process-owner"
    assert settings.public_origin == "http://localhost:8080"
    assert settings.database_url == "postgresql://office:password@localhost:5432/office"


def test_dotenv_path_handles_container_image_layout() -> None:
    assert config._dotenv_path(Path("/app/app/config.py")) == Path("/app/.env")
