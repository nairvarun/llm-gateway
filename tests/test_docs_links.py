import re
from pathlib import Path


def test_local_documentation_links_resolve() -> None:
    root = Path(__file__).resolve().parents[1]
    documents = [root / name for name in ("README.md", "LLM-Reliability-Gateway-Spec.md")]
    documents.extend((root / "docs").rglob("*.md"))
    missing = []
    for document in documents:
        for target in re.findall(r"\[[^]]+\]\(([^)]+)\)", document.read_text()):
            if target.startswith(("http://", "https://", "mailto:", "#")):
                continue
            path = target.split("#", 1)[0]
            if path and not (document.parent / path).is_file():
                missing.append(f"{document.relative_to(root)} -> {target}")
    assert missing == []
