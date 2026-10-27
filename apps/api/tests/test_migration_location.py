from app.infrastructure.postgres.migrate import MIGRATION_DIR, MIGRATION_NAME


def test_postgres_adapter_resolves_versioned_migrations() -> None:
    migration_files = sorted(MIGRATION_DIR.glob("*.sql"))

    assert migration_files
    assert all(MIGRATION_NAME.fullmatch(path.name) for path in migration_files)
