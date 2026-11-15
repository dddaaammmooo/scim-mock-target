"""SCIM filter parser (RFC 7644 3.4.2.2) and in-memory evaluator."""
import json
import re

from .errors import ScimError

URN_RE = re.compile(r"^(urn:.*?:\d+(?:\.\d+)*:[A-Za-z]+)(?::(.*))?$")
CORE_URNS = {"urn:ietf:params:scim:schemas:core:2.0:user", "urn:ietf:params:scim:schemas:core:2.0:group"}
OPS = {"eq", "ne", "co", "sw", "ew", "gt", "ge", "lt", "le"}
TOKEN_RE = re.compile(r'\s*(?:(\()|(\))|(\[)|(\])|("(?:[^"\\]|\\.)*")|([^\s()\[\]"]+))')


def _bad(msg):
    return ScimError(400, f"Invalid filter: {msg}", "invalidFilter")


def tokenize(s: str) -> list[str]:
    out, pos = [], 0
    s = s.rstrip()
    while pos < len(s):
        m = TOKEN_RE.match(s, pos)
        if not m or m.end() == pos:
            raise _bad(f"unexpected character at {pos}")
        out.append(m.group(m.lastindex))
        pos = m.end()
    return out


class _Parser:
    def __init__(self, tokens):
        self.t = tokens
        self.i = 0

    def peek(self):
        return self.t[self.i] if self.i < len(self.t) else None

    def next(self):
        tok = self.peek()
        if tok is None:
            raise _bad("unexpected end of filter")
        self.i += 1
        return tok

    def parse(self):
        node = self.or_()
        if self.peek() is not None:
            raise _bad(f"unexpected token '{self.peek()}'")
        return node

    def or_(self):
        node = self.and_()
        while (self.peek() or "").lower() == "or":
            self.next()
            node = ("or", node, self.and_())
        return node

    def and_(self):
        node = self.not_()
        while (self.peek() or "").lower() == "and":
            self.next()
            node = ("and", node, self.not_())
        return node

    def not_(self):
        if (self.peek() or "").lower() == "not":
            self.next()
            if self.peek() != "(":
                raise _bad("'not' must be followed by '('")
            return ("not", self.primary())
        return self.primary()

    def primary(self):
        tok = self.next()
        if tok == "(":
            node = self.or_()
            if self.next() != ")":
                raise _bad("expected ')'")
            return node
        if tok in (")", "[", "]") or tok.startswith('"'):
            raise _bad(f"unexpected token '{tok}'")
        path = tok
        if self.peek() == "[":
            self.next()
            inner = self.or_()
            if self.next() != "]":
                raise _bad("expected ']'")
            return ("vp", path, inner)
        op = self.next().lower()
        if op == "pr":
            return ("pr", path)
        if op not in OPS:
            raise _bad(f"unknown operator '{op}'")
        return ("cmp", path, op, self.literal(self.next()))

    @staticmethod
    def literal(tok):
        if tok.startswith('"'):
            try:
                return json.loads(tok)
            except ValueError:
                raise _bad(f"bad string literal {tok}")
        low = tok.lower()
        if low == "true":
            return True
        if low == "false":
            return False
        if low == "null":
            return None
        try:
            return int(tok) if re.fullmatch(r"-?\d+", tok) else float(tok)
        except ValueError:
            raise _bad(f"bad value '{tok}'")


def parse(expr: str):
    return _Parser(tokenize(expr)).parse()


def find_key(d: dict, name: str):
    if name in d:
        return name
    low = name.lower()
    for k in d:
        if k.lower() == low:
            return k
    return None


def resolve(obj, path: str) -> list:
    """All values reachable at `path`, flattening multi-valued attributes."""
    m = URN_RE.match(path)
    if m:
        urn, rest = m.group(1), m.group(2)
        if urn.lower() in CORE_URNS:
            cur = [obj]
        else:
            k = find_key(obj, urn) if isinstance(obj, dict) else None
            if k is None:
                return []
            cur = [obj[k]]
        segs = rest.split(".") if rest else []
    else:
        cur, segs = [obj], path.split(".")
    for seg in segs:
        nxt = []
        for v in cur:
            if isinstance(v, list):
                items = v
            else:
                items = [v]
            for it in items:
                if isinstance(it, dict):
                    k = find_key(it, seg)
                    if k is not None:
                        nxt.append(it[k])
        cur = nxt
    out = []
    for v in cur:
        out.extend(v if isinstance(v, list) else [v])
    return out


def _cmp(v, op, lit) -> bool:
    if op in ("co", "sw", "ew"):
        if v is None or isinstance(v, (dict, list)) or lit is None:
            return False
        a, b = str(v).casefold(), str(lit).casefold()
        return b in a if op == "co" else a.startswith(b) if op == "sw" else a.endswith(b)
    if isinstance(v, str) and isinstance(lit, str):
        a, b = v.casefold(), lit.casefold()
    elif isinstance(v, bool) or isinstance(lit, bool):
        if not (isinstance(v, bool) and isinstance(lit, bool)):
            return False
        a, b = v, lit
    elif isinstance(v, (int, float)) and isinstance(lit, (int, float)):
        a, b = v, lit
    elif v is None or lit is None:
        return op == "eq" and v is None and lit is None
    else:
        return False
    return {"eq": a == b, "ne": a != b, "gt": a > b, "ge": a >= b, "lt": a < b, "le": a <= b}[op]


def match(node, obj) -> bool:
    kind = node[0]
    if kind == "and":
        return match(node[1], obj) and match(node[2], obj)
    if kind == "or":
        return match(node[1], obj) or match(node[2], obj)
    if kind == "not":
        return not match(node[1], obj)
    vals = resolve(obj, node[1])
    if kind == "pr":
        return any(v not in (None, "", [], {}) for v in vals)
    if kind == "vp":
        return any(isinstance(v, dict) and match(node[2], v) for v in vals)
    _, _, op, lit = node
    if not vals:  # an absent attribute behaves like null
        return (op == "eq") == (lit is None) if op in ("eq", "ne") else False
    if op == "ne":
        return not any(_cmp(v, "eq", lit) for v in vals)
    return any(_cmp(v, op, lit) for v in vals)
