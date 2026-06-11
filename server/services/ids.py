import re
import uuid


ID_PATTERN = re.compile(r"^(doc|page|job|step|art|evt|q|ans)_[0-9a-f]{32}$")


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def valid_id(value: str, prefix: str | None = None) -> bool:
    if not ID_PATTERN.fullmatch(value or ""):
        return False
    return prefix is None or value.startswith(f"{prefix}_")
