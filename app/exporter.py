import os
from datetime import datetime, timezone
from pathlib import Path
import git

VAULT_PATH = Path("data/vault")

def export_to_markdown(entry) -> str:
    folder = VAULT_PATH / "notes"
    folder.mkdir(parents=True, exist_ok=True)

    filename = f"{entry.id:04d}-{entry.type}-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}.md"
    filepath = folder / filename

    tags_list = "\n".join([f"  - {t}" for t in entry.tags.split(",") if t])
    content = f"""---
id: {entry.id}
type: {entry.type}
origin: {entry.origin}
created: {entry.created_at.isoformat()}
tags:
{tags_list}
---

# Entrada #{entry.id}

{entry.summary if entry.summary else entry.content}

---
> **Fuente original:** {entry.content[:200]}
"""
    filepath.write_text(content, encoding="utf-8")
    _git_commit(str(filepath), f"add: entry #{entry.id} [{entry.type}]")
    return str(filepath)


def _git_commit(filepath: str, message: str):
    try:
        repo = git.Repo(".")
        repo.index.add([filepath])
        repo.index.commit(message)
    except Exception:
        pass  # Si no hay repo git, no bloquea
