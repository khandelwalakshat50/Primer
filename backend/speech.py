import re

_LATEX_BLOCK = re.compile(r"\$\$[\s\S]*?\$\$|\\\[[\s\S]*?\\\]")
_LATEX_INLINE = re.compile(r"\$[^$\n]+\$|\\\([\s\S]*?\\\)")
_LATEX_CMD = re.compile(r"\\[a-zA-Z]+\*?\{[^{}]*\}")
_EQ_SPAN = re.compile(
    r"(?:(?<=\s)|^)[A-Za-z0-9][A-Za-z0-9_]*\s*=\s*[^=\n]{1,80}?(?=(?:[.,;:!?]\s)|\n|$)"
)
_HEAVY_MATH = re.compile(r"[+\-*/^=_√∑∫∞≤≥≈≠×÷]")
_SENTENCE = re.compile(r".+?(?:[.!?]['\"]?(?:\s+|$)|(?:\n\n))", re.S)


def for_speech(text: str) -> str:
    cleaned = _LATEX_BLOCK.sub(" ", text)
    cleaned = _LATEX_INLINE.sub(" ", cleaned)
    cleaned = _LATEX_CMD.sub(" ", cleaned)
    cleaned = _EQ_SPAN.sub(" ", cleaned)
    kept: list[str] = []
    for raw in re.split(r"(?<=[.!?])\s+|\n+", cleaned):
        piece = raw.strip()
        if not piece:
            continue
        letters = len(re.findall(r"[A-Za-z]", piece))
        math = len(_HEAVY_MATH.findall(piece))
        if math >= 3 and math >= letters * 0.35:
            continue
        kept.append(piece)
    return re.sub(r"\s+", " ", " ".join(kept)).strip()


def next_sentences(buffer: str, cursor: int) -> tuple[list[str], int]:
    region = buffer[cursor:]
    found: list[str] = []
    consumed = 0
    for match in _SENTENCE.finditer(region):
        found.append(match.group(0).strip())
        consumed = match.end()
    return found, cursor + consumed
