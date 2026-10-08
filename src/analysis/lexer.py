"""Tokenizer for the supported COBOL subset.

Works on resolved, comment-free lines from :mod:`src.ingest.source`, so every
token knows its original file and line.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, Literal

from src.ingest.source import SrcLine

Kind = Literal["WORD", "NUM", "STR", "PERIOD", "LPAREN", "RPAREN", "OP", "PIC"]


@dataclass(frozen=True)
class Token:
    kind: Kind
    value: str          # WORD upper-cased; STR without quotes; NUM as written
    file: str
    line: int

    def is_word(self, *words: str) -> bool:
        return self.kind == "WORD" and self.value in words

    def __repr__(self) -> str:   # compact in test failures
        return f"{self.kind}:{self.value}@{self.line}"


class LexError(Exception):
    pass


_WORD = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9_-]*[A-Za-z0-9_])?")
_DECIMAL_TAIL = re.compile(r"\.\d+")
_SIGNED_NUM = re.compile(r"[+-](?:\d+(?:\.\d+)?|\.\d+)")
_OPS = (">=", "<=", "<>", "**", ">", "<", "=", "+", "-", "*", "/", ":", "&")


def tokenize(lines: Iterable[SrcLine]) -> list[Token]:
    tokens: list[Token] = []
    expect_pic = False
    for ln in lines:
        text, i, n = ln.text, 0, len(ln.text)
        while i < n:
            ch = text[i]
            if ch.isspace() or ch in ",;":
                i += 1
                continue
            if expect_pic:
                if text[i:i + 3].upper() == "IS " or text[i:].upper() == "IS":
                    i += 2
                    continue
                m = re.compile(r"\S+").match(text, i)
                pic = m.group(0)
                i = m.end()
                period = pic.endswith(".") and (i >= n or text[i].isspace())
                tokens.append(Token("PIC", pic[:-1] if period else pic, ln.file, ln.line))
                if period:
                    tokens.append(Token("PERIOD", ".", ln.file, ln.line))
                expect_pic = False
                continue
            if ch in "'\"":
                value, i = _read_string(text, i, ln)
                tokens.append(Token("STR", value, ln.file, ln.line))
                continue
            if ch == "." and (i + 1 >= n or text[i + 1].isspace()):
                tokens.append(Token("PERIOD", ".", ln.file, ln.line))
                i += 1
                continue
            if ch in "+-" and (i == 0 or text[i - 1].isspace() or text[i - 1] == "("):
                m = _SIGNED_NUM.match(text, i)
                if m and (m.end() >= n or not (text[m.end()].isalnum() or text[m.end()] == "-")):
                    tokens.append(Token("NUM", m.group(0), ln.file, ln.line))
                    i = m.end()
                    continue
            if ch == "." and i + 1 < n and text[i + 1].isdigit():
                m = _DECIMAL_TAIL.match(text, i)
                tokens.append(Token("NUM", "0" + m.group(0), ln.file, ln.line))
                i = m.end()
                continue
            if ch.isalnum():
                m = _WORD.match(text, i)
                word = m.group(0)
                end = m.end()
                # hex / national literal prefixes: X'..', N'..', Z'..'
                if word.upper() in ("X", "N", "Z", "NX", "G") and end < n and text[end] in "'\"":
                    value, i = _read_string(text, end, ln)
                    tokens.append(Token("STR", value, ln.file, ln.line))
                    continue
                if word.isdigit():
                    tail = _DECIMAL_TAIL.match(text, end)
                    if tail:
                        word += tail.group(0)
                        end = tail.end()
                    tokens.append(Token("NUM", word, ln.file, ln.line))
                else:
                    upper = word.upper()
                    tokens.append(Token("WORD", upper, ln.file, ln.line))
                    if upper in ("PIC", "PICTURE"):
                        expect_pic = True
                i = end
                continue
            if ch == "(":
                tokens.append(Token("LPAREN", "(", ln.file, ln.line))
                i += 1
                continue
            if ch == ")":
                tokens.append(Token("RPAREN", ")", ln.file, ln.line))
                i += 1
                continue
            for op in _OPS:
                if text.startswith(op, i):
                    tokens.append(Token("OP", op, ln.file, ln.line))
                    i += len(op)
                    break
            else:
                if ch == "=" or ch == ".":
                    tokens.append(Token("OP", ch, ln.file, ln.line))
                i += 1          # skip characters outside the subset rather than fail
    return tokens


def _read_string(text: str, i: int, ln: SrcLine) -> tuple[str, int]:
    quote = text[i]
    i += 1
    buf: list[str] = []
    while i < len(text):
        if text[i] == quote:
            if i + 1 < len(text) and text[i + 1] == quote:   # doubled quote escape
                buf.append(quote)
                i += 2
                continue
            return "".join(buf), i + 1
        buf.append(text[i])
        i += 1
    raise LexError(f"unterminated literal at {ln.file}:{ln.line}")
