from runner import RuntimeLauncherClient


def test_ready_defaults_missing_gateway_flag_without_blocking_provider() -> None:
    class Client:
        def get(self, path: str) -> dict[str, object]:
            assert path == "/health/ready"
            return {"status": "ready", "provider_ready": True}

    launcher = object.__new__(RuntimeLauncherClient)
    launcher._client = Client()

    assert launcher.ready() == {
        "status": "ready",
        "provider_ready": True,
        "gateway_ready": False,
    }
