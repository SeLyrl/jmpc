import re
from dataclasses import dataclass
from typing import Any


@dataclass
class RonVariant:
    """Representa constructores o enums de RON como Split(...), Pane(Queue) o Some(...)."""
    name: str
    value: Any = None


class RonTokenizer:
    TOKEN_SPEC = [
        ("COMMENT", r"//[^\n]*|/\*[\s\S]*?\*/"),
        ("ATTRIBUTE", r"#!\[[^\]]*\]"),
        ("STRING", r'"(?:\\.|[^"\\])*"'),
        ("NUMBER", r"-?\d+(?:\.\d+)?"),
        ("LPAREN", r"\("),
        ("RPAREN", r"\)"),
        ("LBRACKET", r"\["),
        ("RBRACKET", r"\]"),
        ("LBRACE", r"\{"),
        ("RBRACE", r"\}"),
        ("COLON", r":"),
        ("COMMA", r","),
        ("IDENT", r"[A-Za-z_][A-Za-z0-9_]*"),
        ("SKIP", r"[ \t\r\n]+"),
    ]

    def __init__(self, text: str):
        regex = "|".join(f"(?P<{name}>{pattern})" for name, pattern in self.TOKEN_SPEC)
        self.tokens: list[tuple[str, str]] = []
        for match in re.finditer(regex, text):
            kind = match.lastgroup
            val = match.group()
            if kind in ("SKIP", "COMMENT", "ATTRIBUTE"):
                continue
            self.tokens.append((kind, val))
        self.pos = 0

    def peek(self) -> tuple[str, str] | None:
        return self.tokens[self.pos] if self.pos < len(self.tokens) else None

    def next(self) -> tuple[str, str]:
        tok = self.tokens[self.pos]
        self.pos += 1
        return tok


class Ronloader:
    def __init__(self, text: str):
        self.lexer = RonTokenizer(text)

    def parse(self) -> Any:
        if not self.lexer.peek():
            return {}
        return self.parse_value()

    def parse_value(self) -> Any:
        tok = self.lexer.peek()
        if not tok:
            return None
        kind, val = tok

        if kind == "STRING":
            self.lexer.next()
            return bytes(val[1:-1], "utf-8").decode("unicode_escape")
        elif kind == "NUMBER":
            self.lexer.next()
            return float(val) if "." in val else int(val)
        elif kind == "LBRACKET":
            return self.parse_list()
        elif kind == "LBRACE":
            return self.parse_map()
        elif kind == "LPAREN":
            return self.parse_struct_or_tuple()
        elif kind == "IDENT":
            self.lexer.next()
            if val == "true":
                return True
            if val == "false":
                return False
            if val == "None":
                return None
            if self.lexer.peek() and self.lexer.peek()[0] == "LPAREN":
                inner = self.parse_struct_or_tuple()
                if val == "Some":
                    return inner
                return RonVariant(val, inner)
            return RonVariant(val)
        else:
            self.lexer.next()
            return val

    def parse_list(self) -> list:
        self.lexer.next()  # [
        items = []
        while self.lexer.peek() and self.lexer.peek()[0] != "RBRACKET":
            if self.lexer.peek()[0] == "COMMA":
                self.lexer.next()
                continue
            items.append(self.parse_value())
        if self.lexer.peek() and self.lexer.peek()[0] == "RBRACKET":
            self.lexer.next()
        return items

    def parse_map(self) -> dict:
        self.lexer.next()  # {
        mapping = {}
        while self.lexer.peek() and self.lexer.peek()[0] != "RBRACE":
            if self.lexer.peek()[0] == "COMMA":
                self.lexer.next()
                continue
            k = self.parse_value()
            if self.lexer.peek() and self.lexer.peek()[0] == "COLON":
                self.lexer.next()
            v = self.parse_value()
            mapping[str(k)] = v
        if self.lexer.peek() and self.lexer.peek()[0] == "RBRACE":
            self.lexer.next()
        return mapping

    def parse_struct_or_tuple(self) -> Any:
        self.lexer.next()  # (
        if self.lexer.peek() and self.lexer.peek()[0] == "RPAREN":
            self.lexer.next()
            return {}

        # Determinar si es struct con claves (key: val) o tupla posicional
        saved_pos = self.lexer.pos
        is_struct = False
        first_tok = self.lexer.peek()
        if first_tok and first_tok[0] == "IDENT":
            self.lexer.next()
            if self.lexer.peek() and self.lexer.peek()[0] == "COLON":
                is_struct = True
        self.lexer.pos = saved_pos

        if is_struct:
            obj = {}
            while self.lexer.peek() and self.lexer.peek()[0] != "RPAREN":
                if self.lexer.peek()[0] == "COMMA":
                    self.lexer.next()
                    continue
                k_tok = self.lexer.next()
                key = k_tok[1]
                if self.lexer.peek() and self.lexer.peek()[0] == "COLON":
                    self.lexer.next()
                obj[key] = self.parse_value()
            if self.lexer.peek() and self.lexer.peek()[0] == "RPAREN":
                self.lexer.next()
            return obj
        else:
            items = []
            while self.lexer.peek() and self.lexer.peek()[0] != "RPAREN":
                if self.lexer.peek()[0] == "COMMA":
                    self.lexer.next()
                    continue
                items.append(self.parse_value())
            if self.lexer.peek() and self.lexer.peek()[0] == "RPAREN":
                self.lexer.next()
            return items[0] if len(items) == 1 else items


def load_ron_file(path) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        data = Ronloader(f.read()).parse()
    return data if isinstance(data, dict) else {}