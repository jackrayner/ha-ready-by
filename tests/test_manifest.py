import json
from pathlib import Path

ROOT_DIR = Path(__file__).parent.parent
COMPONENT_DIR = ROOT_DIR / "custom_components" / "ready_by"


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_manifest_version_matches_release_please():
    manifest = _load(COMPONENT_DIR / "manifest.json")
    release = _load(ROOT_DIR / ".release-please-manifest.json")
    assert manifest["version"] == release["."]


def test_no_runtime_requirements():
    """The model is pure Python; keep manifest and requirements.txt both empty."""
    manifest = _load(COMPONENT_DIR / "manifest.json")
    requirements = (ROOT_DIR / "requirements.txt").read_text(encoding="utf-8").split()
    assert manifest["requirements"] == []
    assert requirements == []


def test_every_service_is_translated():
    services = (COMPONENT_DIR / "services.yaml").read_text(encoding="utf-8")
    names = [line[:-1] for line in services.splitlines() if line and not line[0].isspace()]
    translated = _load(COMPONENT_DIR / "translations" / "en.json")["services"]
    assert sorted(names) == sorted(translated)


def test_manifest_keys_sorted_for_hassfest():
    keys = list(_load(COMPONENT_DIR / "manifest.json"))
    assert keys[:2] == ["domain", "name"]
    assert keys[2:] == sorted(keys[2:])
