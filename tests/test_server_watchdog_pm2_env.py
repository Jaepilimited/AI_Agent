"""Regression coverage for Windows PM2 named-pipe isolation."""

from scripts import server_watchdog


def test_watchdog_builds_isolated_pm2_subprocess_environment():
    builder = getattr(server_watchdog, "build_pm2_subprocess_env", None)
    assert callable(builder), "watchdog must provide a PM2 subprocess environment builder"

    env = builder({"KEEP_ME": "yes"})

    assert env["KEEP_ME"] == "yes"
    assert env["PM2_DAEMON_RPC_PORT"] == r"\\.\pipe\skin1004-db-pc-rpc.sock"
    assert env["PM2_DAEMON_PUB_PORT"] == r"\\.\pipe\skin1004-db-pc-pub.sock"
    assert env["PM2_INTERACTOR_RPC_PORT"] == r"\\.\pipe\skin1004-db-pc-interactor.sock"
