# -*- coding: utf-8 -*-
"""배포가 **서버가 스스로 쓰는 상태**를 로컬 사본으로 덮지 않는다 — 2026-09-15.

두 가지를 함께 지킨다:
  1. 그 파일들이 전송 목록에 없을 것
  2. `EXCLUDE_PATHS` 가 **파일에도** 걸릴 것 — 오래 디렉토리에만 걸려 있어서
     파일 하나를 지목해 적어도 아무 일이 없었다. 에러가 아니라 조용히 전송됐다.
"""

import importlib.util
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
_SCRIPT = _ROOT / "scripts" / "deploy_new_server.py"

# 서버가 자기 손으로 쓰는 것들. 로컬본으로 덮으면 조용히 깨진다.
_SERVER_OWNED = (
    "data/gws_tokens",                 # 구글 OAuth — refresh_token 이 회전하면 연결이 끊긴다
    "data/notion_vectors_gemini.json",  # 노션 색인 맵 — 낡으면 멀쩡한 청크를 지우는 근거가 된다
    "data/notion_status_block_id.json",
)


def _module():
    spec = importlib.util.spec_from_file_location("deploy_new_server", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.mark.parametrize("path", _SERVER_OWNED)
def test_server_owned_state_is_registered_as_excluded(path: str) -> None:
    assert path in _module().EXCLUDE_PATHS


def test_payload_carries_no_server_owned_state() -> None:
    """⛔ 목록에서 판정한다 — 규칙만 적어 두고 목록을 안 보면 조용히 낡는다."""
    payload = [str(p).replace("\\", "/") for p in _module().collect()]

    leaked = [p for p in payload
              if "/data/gws_tokens/" in p
              or p.endswith("/data/notion_vectors_gemini.json")
              or p.endswith("/data/notion_status_block_id.json")]

    assert leaked == [], f"서버 상태 파일이 전송 목록에 있다: {leaked[:5]}"


def test_exclude_paths_applies_to_files_not_only_directories(tmp_path) -> None:
    """파일 단위 제외가 실제로 듣는지 — 안 들으면 적어 둔 규칙이 장식이 된다."""
    mod = _module()

    root = tmp_path / "proj"
    (root / "data").mkdir(parents=True)
    (root / "data" / "keep.json").write_text("{}", encoding="utf-8")
    (root / "data" / "drop.json").write_text("{}", encoding="utf-8")

    mod.PROJ = root
    mod.EXCLUDE_PATHS = {"data/drop.json"}

    names = {Path(p).name for p in mod.collect()}

    assert "keep.json" in names
    assert "drop.json" not in names, "EXCLUDE_PATHS 가 파일에는 안 걸린다"


def test_deploy_still_ships_source() -> None:
    """제외를 넓히다 소스를 빠뜨리지 않았는지 — `app/knowledge_map/` 사고의 반대편."""
    payload = [str(p).replace("\\", "/") for p in _module().collect()]

    for must in ("app/core/llm.py", "app/core/golden_runner.py",
                 "app/agents/orchestrator.py", "data/golden_set.json"):
        assert any(p.endswith(must) for p in payload), f"{must} 가 전송 목록에서 빠졌다"
