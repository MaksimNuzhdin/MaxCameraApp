"""Minimal GenApi 1.1 evaluator for the node types used by the MaxCamera XML.

Formulas come from the XML and are parsed by a small expression compiler; they
are never handed to eval().
"""
import math
import re
import xml.etree.ElementTree as ET

_TOKEN = re.compile(r"\s*(0[xX][0-9a-fA-F]+|\d+\.\d*(?:[eE][-+]?\d+)?|\.\d+|\d+|"
                    r"[A-Za-z_][A-Za-z_0-9.]*|\*\*|\|\||&&|<>|<=|>=|<<|>>|[-+*/%&|^~!?:=<>(),])")
_LEVELS = [["||"], ["&&"], ["|"], ["^"], ["&"], ["=", "<>"], ["<", ">", "<=", ">="],
           ["<<", ">>"], ["+", "-"], ["*", "/", "%"]]
_FUNCS = {"ABS": abs, "SQRT": math.sqrt, "FLOOR": math.floor, "CEIL": math.ceil,
          "ROUND": round, "TRUNC": math.trunc, "SGN": lambda x: (x > 0) - (x < 0)}


def _binary(op, a, b, integer):
    if op == "/":
        if b == 0:
            raise ZeroDivisionError("division by zero in formula")
        return int(a / b) if integer else a / b
    if op == "%":
        return a % b
    return {"||": lambda: int(bool(a) or bool(b)), "&&": lambda: int(bool(a) and bool(b)),
            "|": lambda: int(a) | int(b), "^": lambda: int(a) ^ int(b), "&": lambda: int(a) & int(b),
            "=": lambda: int(a == b), "<>": lambda: int(a != b), "<": lambda: int(a < b),
            ">": lambda: int(a > b), "<=": lambda: int(a <= b), ">=": lambda: int(a >= b),
            "<<": lambda: int(a) << int(b), ">>": lambda: int(a) >> int(b), "+": lambda: a + b,
            "-": lambda: a - b, "*": lambda: a * b}[op]()


class Formula:
    def __init__(self, text, integer=False):
        self.tokens = []
        at = 0
        text = text.strip()
        while at < len(text):
            m = _TOKEN.match(text, at)
            if not m:
                raise ValueError(f"bad formula near {text[at:at + 20]!r}")
            self.tokens.append(m.group(1))
            at = m.end()
        self.at = 0
        self.integer = integer
        self.root = self._ternary()
        if self.at != len(self.tokens):
            raise ValueError(f"trailing tokens in formula {text!r}")

    def __call__(self, env):
        return self.root(env)

    def _peek(self):
        return self.tokens[self.at] if self.at < len(self.tokens) else None

    def _ternary(self):
        cond = self._level(0)
        if self._peek() != "?":
            return cond
        self.at += 1
        yes = self._ternary()
        if self._peek() != ":":
            raise ValueError("expected ':' in formula")
        self.at += 1
        no = self._ternary()
        return lambda e: yes(e) if cond(e) else no(e)

    def _level(self, n):
        if n == len(_LEVELS):
            return self._power()
        left = self._level(n + 1)
        while self._peek() in _LEVELS[n]:
            op = self.tokens[self.at]
            self.at += 1
            right = self._level(n + 1)
            left = (lambda o, a, b: lambda e: _binary(o, a(e), b(e), self.integer))(op, left, right)
        return left

    def _power(self):
        base = self._unary()
        if self._peek() == "**":
            self.at += 1
            exponent = self._power()
            return lambda e: base(e) ** exponent(e)
        return base

    def _unary(self):
        t = self._peek()
        if t in ("-", "+", "!", "~"):
            self.at += 1
            v = self._unary()
            return {"-": lambda e: -v(e), "+": v, "!": lambda e: int(not v(e)),
                    "~": lambda e: ~int(v(e))}[t]
        return self._atom()

    def _atom(self):
        t = self._peek()
        if t is None:
            raise ValueError("unexpected end of formula")
        self.at += 1
        if t == "(":
            v = self._ternary()
            if self._peek() != ")":
                raise ValueError("expected ')' in formula")
            self.at += 1
            return v
        if t[0].isdigit() or t[0] == ".":
            value = int(t, 16) if t[:2].lower() == "0x" else (float(t) if any(c in t for c in ".eE") else int(t))
            return lambda e: value
        if self._peek() == "(" and t.upper() in _FUNCS:
            self.at += 1
            arg = self._ternary()
            if self._peek() != ")":
                raise ValueError("expected ')' in formula")
            self.at += 1
            f = _FUNCS[t.upper()]
            return lambda e: f(arg(e))
        return lambda e: e[t]


class ReadFailed(Exception):
    pass


def _tag(e):
    return e.tag.split("}")[-1]


def _text(e, name, default=None):
    for c in e:
        if _tag(c) == name:
            return (c.text or "").strip()
    return default


def _number(text):
    if text is None:
        return None
    return int(text, 16) if text.lower().startswith("0x") else (float(text) if any(c in text for c in ".eE") else int(text))


class Device:
    """Parsed node map. `port` provides read_reg, write_reg and read_mem."""

    def __init__(self, xml_bytes, port):
        self.port = port
        self.nodes = {}
        self.categories = []
        root = ET.fromstring(xml_bytes)
        for e in root:
            if e.get("Name"):
                self.nodes[e.get("Name")] = e
        self._formulas = {}
        for e in root:
            if _tag(e) == "Category" and e.get("Name") != "Root":
                self.categories.append((e.get("Name"), [(c.text or "").strip() for c in e if _tag(c) == "pFeature"]))

    # ---- evaluation -----------------------------------------------------
    def _formula(self, node, field):
        key = (node.get("Name"), field)
        if key not in self._formulas:
            self._formulas[key] = Formula(_text(node, field), integer=_tag(node) == "IntSwissKnife")
        return self._formulas[key]

    def _vars(self, node, extra=None):
        env = dict(extra or {})
        for c in node:
            if _tag(c) == "pVariable":
                env[c.get("Name")] = self.value((c.text or "").strip())
        return env

    def _reg_address(self, node):
        address = _number(_text(node, "Address", "0"))
        if _text(node, "pAddress"):
            address += self.value(_text(node, "pAddress"))
        return address

    def _read_int_reg(self, node):
        address = self._reg_address(node)
        length = int(_text(node, "Length", "4"))
        if length == 8:
            raw = (self.port.read_reg(address) << 32) | self.port.read_reg(address + 4)
        else:
            raw = self.port.read_reg(address)
        tag = _tag(node)
        if tag == "MaskedIntReg":
            bit = _text(node, "Bit")
            msb, lsb = (int(bit), int(bit)) if bit is not None else (int(_text(node, "MSB")), int(_text(node, "LSB")))
            width = abs(lsb - msb) + 1
            raw = (raw >> (31 - max(msb, lsb))) & ((1 << width) - 1)
        elif _text(node, "Sign") == "Signed" and raw & (1 << (length * 8 - 1)):
            raw -= 1 << (length * 8)
        return raw

    def value(self, name):
        node = self.nodes[name]
        tag = _tag(node)
        if tag in ("IntReg", "MaskedIntReg"):
            return self._read_int_reg(node)
        if tag in ("IntSwissKnife", "SwissKnife"):
            return self._formula(node, "Formula")(self._vars(node))
        if tag == "Converter":
            to = self.value(_text(node, "pValue"))
            return self._formula(node, "FormulaFrom")(self._vars(node, {"TO": to}))
        if tag in ("Integer", "Float", "Enumeration"):
            if _text(node, "pValue"):
                return self.value(_text(node, "pValue"))
            return _number(_text(node, "Value"))
        if tag == "Boolean":
            return int(self.value(_text(node, "pValue")) == _number(_text(node, "OnValue", "1")))
        if tag == "StringReg":
            size = int(_text(node, "Length"))
            return self.port.read_mem(self._reg_address(node), size).split(b"\0")[0].decode("ascii", "replace")
        raise ReadFailed(f"{name}: unreadable {tag}")

    def set_value(self, name, value):
        node = self.nodes[name]
        tag = _tag(node)
        if tag in ("IntReg", "MaskedIntReg"):
            if tag == "MaskedIntReg":
                raise ReadFailed(f"{name}: masked registers are not writable through this interface")
            self.port.write_reg(self._reg_address(node), int(round(value)) & 0xffffffff)
        elif tag == "Converter":
            raw = self._formula(node, "FormulaTo")(self._vars(node, {"FROM": value}))
            self.set_value(_text(node, "pValue"), raw)
        elif tag in ("Integer", "Float", "Enumeration"):
            self.set_value(_text(node, "pValue"), value)
        elif tag == "Boolean":
            on, off = _number(_text(node, "OnValue", "1")), _number(_text(node, "OffValue", "0"))
            self.set_value(_text(node, "pValue"), on if value else off)
        elif tag == "Command":
            self.set_value(_text(node, "pValue"), _number(_text(node, "CommandValue", "1")))
        else:
            raise ReadFailed(f"{name}: {tag} is not writable through this interface")

    def _flag(self, node, field, default):
        ref = _text(node, field)
        if not ref:
            return default
        try:
            return bool(self.value(ref))
        except (ReadFailed, KeyError, OSError, ZeroDivisionError):
            return default

    def _bound(self, node, field):
        ref = _text(node, "p" + field)
        if ref:
            try:
                return self.value(ref)
            except (ReadFailed, KeyError, OSError, ZeroDivisionError):
                return None
        return _number(_text(node, field))

    def _access(self, node):
        imposed = _text(node, "ImposedAccessMode")
        if imposed:
            return imposed
        tag = _tag(node)
        if tag == "Command":
            return "WO"
        direct = _text(node, "AccessMode")
        if direct:
            return direct
        ref = _text(node, "pValue")
        return self._access(self.nodes[ref]) if ref and ref in self.nodes else ("RO" if tag.endswith("Knife") else "RW")

    # ---- description ----------------------------------------------------
    def describe(self, name):
        node = self.nodes[name]
        tag = _tag(node)
        kinds = {"Integer": "int", "Float": "float", "Enumeration": "enum", "Boolean": "bool",
                 "Command": "command", "StringReg": "string"}
        if tag not in kinds:
            return None
        info = {"name": name, "kind": kinds[tag], "access": self._access(node),
                "description": _text(node, "Description", ""), "unit": _text(node, "Unit", ""),
                "visibility": _text(node, "Visibility", "Beginner"),
                "representation": _text(node, "Representation", ""),
                "selected": [(c.text or "").strip() for c in node if _tag(c) == "pSelected"]}
        if tag == "Enumeration":
            info["entries"] = [
                {"name": c.get("Name"), "value": _number(_text(c, "Value")),
                 "hidden": _text(c, "Visibility") == "Invisible"}
                for c in node if _tag(c) == "EnumEntry"]
        return info

    def state(self, name):
        node = self.nodes[name]
        tag = _tag(node)
        out = {"available": self._flag(node, "pIsAvailable", True),
               "locked": self._flag(node, "pIsLocked", False)}
        if tag == "Integer" or tag == "Float":
            out["min"] = self._bound(node, "Min")
            out["max"] = self._bound(node, "Max")
            if tag == "Integer":
                out["inc"] = self._bound(node, "Inc")
        if self._access(node) != "WO" and tag != "Command":
            try:
                out["value"] = self.value(name)
            except (ReadFailed, KeyError, OSError, ZeroDivisionError) as error:
                out["value"] = None
                out["available"] = False
                out["error"] = str(error)
        if tag == "Enumeration":
            current = out.get("value")
            label = None
            unavailable = []
            for c in node:
                if _tag(c) != "EnumEntry":
                    continue
                if _number(_text(c, "Value")) == current and label is None:
                    label = c.get("Name")
                if not self._flag(c, "pIsAvailable", True):
                    unavailable.append(c.get("Name"))
            out["value"] = label if label is not None else current
            out["unavailable"] = unavailable
        return out

    def readable_addresses(self, names=None):
        nodes = self.nodes.values()
        if names is not None:
            # Named transactions (AI selectors/latches) must never prefetch a
            # different selector's object fields or reuse an earlier snapshot.
            selected = {}
            def include(name):
                if name in selected or name not in self.nodes:
                    return
                node = self.nodes[name]
                selected[name] = node
                for child in node:
                    if _tag(child).startswith("p") and child.text:
                        include(child.text.strip())
            for name in names:
                include(name)
            nodes = selected.values()
        out = []
        for node in nodes:
            if _tag(node) in ("IntReg", "MaskedIntReg") and _text(node, "AccessMode") != "WO" and not _text(node, "pAddress"):
                address = _number(_text(node, "Address", "0"))
                out.append(address)
                if _text(node, "Length") == "8":
                    out.append(address + 4)
        return sorted(set(out))

    def enum_value(self, name, label):
        for c in self.nodes[name]:
            if _tag(c) == "EnumEntry" and c.get("Name") == label:
                return _number(_text(c, "Value"))
        raise ValueError(f"{name} has no entry {label}")
