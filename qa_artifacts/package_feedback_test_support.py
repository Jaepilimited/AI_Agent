"""Package locally installed pure-Python test tools for an isolated WAS check."""
import importlib.metadata
from pathlib import Path
import zipfile

target = Path(__file__).resolve().parent / "feedback166_167_test_support.zip"
distributions = ["pytest", "pluggy", "iniconfig", "packaging", "pygments", "pytest-asyncio", "typing-extensions"]
count = 0
with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
    for name in distributions:
        distribution = importlib.metadata.distribution(name)
        for entry in distribution.files or []:
            if ".." in entry.parts or "__pycache__" in entry.parts or entry.suffix in (".pyc", ".exe"):
                continue
            source = distribution.locate_file(entry)
            if source.is_file():
                archive.write(source, str(entry).replace("\\", "/"))
                count += 1
print({"files": count, "bytes": target.stat().st_size, "artifact": str(target)})
