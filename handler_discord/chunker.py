"""Split long replies into Discord-sized chunks (≤ 2000 chars each).

Code-block aware: if a split lands inside a ``` fence, the current chunk gets a
closing fence and the next chunk gets an opening fence so syntax highlighting
survives the split.
"""

from __future__ import annotations

DISCORD_MAX_LENGTH = 1900  # Hard cap is 2000; leave headroom for formatting drift.


def chunk_for_discord(text: str, *, max_length: int = DISCORD_MAX_LENGTH) -> list[str]:
    if len(text) <= max_length:
        return [text]

    chunks: list[str] = []
    current = ""
    in_code_block = False
    code_fence_lang = ""

    for line in text.split("\n"):
        if len(current) + len(line) + 1 > max_length and current:
            if in_code_block:
                current += "\n```"
            chunks.append(current)
            current = f"```{code_fence_lang}\n{line}" if in_code_block else line
        else:
            current = f"{current}\n{line}" if current else line

        stripped = line.strip()
        if stripped.startswith("```"):
            if in_code_block:
                in_code_block = False
                code_fence_lang = ""
            else:
                in_code_block = True
                code_fence_lang = stripped[3:].strip()

    if current:
        chunks.append(current)
    return chunks or [text]
