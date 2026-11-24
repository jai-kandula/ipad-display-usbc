"""The math pad: write a line on the iPad (Scribble turns your handwriting
into text) and the PC works it out:

  2*sin(30deg) + sqrt(2)          a number
  x^2 - 5x + 6 = 0                solve (for x; several unknowns: for all)
  diff(x^2 sin(x), x)             derivatives, integrate(...), simplify(...),
  integrate(x e^x, x)             expand(...), factor(...), limit(...)
  300 knots to m/s                units: length, mass, time, speed, force,
  14.7 psi in kPa                 pressure, energy, power, density, area,
  20 C to F                       volume, angle, temperature

Algebra needs the free 'sympy' package (start.bat installs it); numbers and
units work without it. Input is checked against a short list of allowed
names before anything reads it, so it can only ever be math."""
import ast
import math
import re

# ---------------------------------------------------------------- units
# name -> (factor to SI, dimension (m, kg, s, K, rad))
_U = {}


def _unit(names, factor, dim):
    for n in names.split():
        _U[n] = (factor, dim)


L, M, T, K, A = (1, 0, 0, 0, 0), (0, 1, 0, 0, 0), (0, 0, 1, 0, 0), (0, 0, 0, 1, 0), (0, 0, 0, 0, 1)
N = (1, 1, -2, 0, 0)
_unit("m meter meters metre metres", 1, L)
_unit("km", 1e3, L)
_unit("cm", 1e-2, L)
_unit("mm", 1e-3, L)
_unit("um micron", 1e-6, L)
_unit("in inch inches", 0.0254, L)
_unit("ft foot feet", 0.3048, L)
_unit("yd yard yards", 0.9144, L)
_unit("mi mile miles", 1609.344, L)
_unit("nmi NM", 1852, L)
_unit("kg", 1, M)
_unit("g gram grams", 1e-3, M)
_unit("mg", 1e-6, M)
_unit("lb lbm lbs pound pounds", 0.45359237, M)
_unit("oz", 0.028349523125, M)
_unit("slug slugs", 14.59390294, M)
_unit("t tonne", 1000, M)
_unit("s sec second seconds", 1, T)
_unit("ms", 1e-3, T)
_unit("min minute minutes", 60, T)
_unit("h hr hour hours", 3600, T)
_unit("day days", 86400, T)
_unit("kt kts knot knots", 1852 / 3600, (1, 0, -1, 0, 0))
_unit("mph", 1609.344 / 3600, (1, 0, -1, 0, 0))
_unit("kph", 1000 / 3600, (1, 0, -1, 0, 0))
_unit("N newton newtons", 1, N)
_unit("kN", 1e3, N)
_unit("lbf", 4.4482216152605, N)
_unit("kgf", 9.80665, N)
_unit("Pa", 1, (-1, 1, -2, 0, 0))
_unit("kPa", 1e3, (-1, 1, -2, 0, 0))
_unit("MPa", 1e6, (-1, 1, -2, 0, 0))
_unit("GPa", 1e9, (-1, 1, -2, 0, 0))
_unit("bar", 1e5, (-1, 1, -2, 0, 0))
_unit("atm", 101325, (-1, 1, -2, 0, 0))
_unit("psi", 6894.757293168, (-1, 1, -2, 0, 0))
_unit("psf", 47.88025898, (-1, 1, -2, 0, 0))
_unit("inHg", 3386.389, (-1, 1, -2, 0, 0))
_unit("mmHg torr", 133.322387415, (-1, 1, -2, 0, 0))
_unit("J joule joules", 1, (2, 1, -2, 0, 0))
_unit("kJ", 1e3, (2, 1, -2, 0, 0))
_unit("cal", 4.184, (2, 1, -2, 0, 0))
_unit("kcal Cal", 4184, (2, 1, -2, 0, 0))
_unit("BTU btu", 1055.05585, (2, 1, -2, 0, 0))
_unit("Wh", 3600, (2, 1, -2, 0, 0))
_unit("kWh", 3.6e6, (2, 1, -2, 0, 0))
_unit("W watt watts", 1, (2, 1, -3, 0, 0))
_unit("kW", 1e3, (2, 1, -3, 0, 0))
_unit("hp", 745.69987158, (2, 1, -3, 0, 0))
_unit("L l liter liters litre", 1e-3, (3, 0, 0, 0, 0))
_unit("mL ml", 1e-6, (3, 0, 0, 0, 0))
_unit("gal gallon gallons", 3.785411784e-3, (3, 0, 0, 0, 0))
_unit("rad radian radians", 1, A)
_unit("deg degree degrees", math.pi / 180, A)
_unit("rev rpm_rev", 2 * math.pi, A)
_unit("rpm", 2 * math.pi / 60, (0, 0, -1, 0, 1))
_unit("Hz", 1, (0, 0, -1, 0, 0))
_unit("K kelvin", 1, K)
_unit("R rankine", 5 / 9, K)
TEMPS = {"C": (1, 273.15), "degC": (1, 273.15), "F": (5 / 9, 459.67 * 5 / 9), "degF": (5 / 9, 459.67 * 5 / 9)}


def _parse_unit(text):
    """'kg/m^3', 'm/s^2', 'N*m', 'ft lbf' -> (factor, dim)."""
    text = text.strip().replace("·", "*").replace(" ", "*")
    f, dim, sign = 1.0, [0] * 5, 1
    for tok in re.findall(r"[*/]|[A-Za-z_]+(?:\^-?\d+|\d)?", text):
        if tok == "*":
            sign = sign if sign else 1
            continue
        if tok == "/":
            sign = -1
            continue
        m = re.fullmatch(r"([A-Za-z_]+)(?:\^(-?\d+)|(\d))?", tok)
        name, p = m.group(1), int(m.group(2) or m.group(3) or 1)
        if name not in _U:
            raise ValueError(f"unknown unit '{name}'")
        uf, ud = _U[name]
        f *= uf ** (p * sign)
        dim = [a + b * p * sign for a, b in zip(dim, ud)]
    return f, tuple(dim)


def convert(text):
    m = re.fullmatch(r"\s*([-+]?[\d.]+(?:e[-+]?\d+)?)\s*([A-Za-z_][\w^*/ ·-]*?)\s+(?:to|in|->)\s+([A-Za-z_][\w^*/ ·-]*)\s*", text)
    if not m:
        return None
    v, a, b = float(m.group(1)), m.group(2).strip(), m.group(3).strip()
    if a in TEMPS or b in TEMPS or (a in ("K", "R") and b in TEMPS) or (b in ("K", "R") and a in TEMPS):
        k = v * TEMPS[a][0] + TEMPS[a][1] if a in TEMPS else v * _U[a][0]
        out = (k - TEMPS[b][1]) / TEMPS[b][0] if b in TEMPS else k / _U[b][0]
        return f"{_num(out)} {b}"
    fa, da = _parse_unit(a)
    fb, db = _parse_unit(b)
    if da != db:
        raise ValueError(f"{a} and {b} measure different things")
    return f"{_num(v * fa / fb)} {b}"


def _num(x):
    if isinstance(x, float):
        if x == 0 or 1e-4 <= abs(x) < 1e12:
            return f"{x:.10g}"
        return f"{x:.6e}"
    return str(x)


# ---------------------------------------------------------------- safety
FUNCS = {"sin", "cos", "tan", "asin", "acos", "atan", "atan2", "sinh", "cosh", "tanh", "sqrt", "cbrt", "exp", "log", "ln",
         "log10", "log2", "abs", "floor", "ceiling", "ceil", "factorial", "pi", "e", "E", "oo", "inf", "I",
         "diff", "integrate", "simplify", "expand", "factor", "limit", "solve", "series", "sec", "csc", "cot",
         "deg", "rad", "Matrix", "det", "summation", "Sum", "nsolve", "evalf", "N", "degrees", "radians"}
NAME = re.compile(r"[A-Za-z_][A-Za-z_0-9]*")


def _check(expr):
    if len(expr) > 400:
        raise ValueError("that's too long for the math pad")
    if not re.fullmatch(r"[\w\s+\-*/^().,=<>!'\[\]]*", expr) or "__" in expr or re.search(r"[A-Za-z_)\]]\s*\.\s*[A-Za-z_]", expr):
        raise ValueError("only numbers, letters, + - * / ^ ( ) = and commas, please")
    for n in NAME.findall(expr):
        if n not in FUNCS and not re.fullmatch(r"[A-Za-z](?:_?[A-Za-z0-9]{0,3})?", n):
            raise ValueError(f"'{n}' isn't something the math pad knows")
        if n in ("eval", "exec", "open", "input", "lambda", "import", "global", "class", "def"):
            raise ValueError(f"'{n}' isn't something the math pad knows")


def _prep(expr):
    expr = expr.replace("×", "*").replace("÷", "/").replace("−", "-").replace("π", "pi").replace("√", "sqrt").replace("²", "^2").replace("³", "^3")
    expr = re.sub(r"(\d+(?:\.\d+)?)\s*deg\b", r"(\1*pi/180)", expr)      # 30deg -> radians
    expr = re.sub(r"\bln\(", "log(", expr)
    return expr


# ---------------------------------------------------------------- the pad
def solve(text):
    """One line from the iPad -> {"answer": str, "kind": ...} or raises ValueError."""
    text = (text or "").strip()
    if not text:
        raise ValueError("write something first")
    u = convert(text)
    if u is not None:
        return {"answer": u, "kind": "units"}
    expr = _prep(text)
    _check(expr)
    try:
        import sympy  # type: ignore
    except ImportError:
        return {"answer": _plain(expr), "kind": "number",
                "note": "algebra (solve, diff, integrate) needs sympy: run start.bat once with internet"}
    return _sym(sympy, expr)


def _sym(sp, expr):
    from sympy.parsing.sympy_parser import (convert_xor, implicit_multiplication_application, parse_expr,
                                            standard_transformations)
    tr = standard_transformations + (implicit_multiplication_application, convert_xor)
    allowed = {k: getattr(sp, k) for k in ("sin", "cos", "tan", "asin", "acos", "atan", "atan2", "sinh", "cosh", "tanh",
                                           "sqrt", "cbrt", "exp", "log", "Abs", "floor", "ceiling", "factorial", "pi", "E",
                                           "oo", "I", "diff", "integrate", "simplify", "expand", "factor", "limit", "solve",
                                           "series", "sec", "csc", "cot", "Matrix", "det", "summation", "Sum", "nsolve", "N")}
    allowed.update(abs=sp.Abs, ceil=sp.ceiling, e=sp.E, inf=sp.oo, log10=lambda x: sp.log(x, 10), log2=lambda x: sp.log(x, 2),
                   degrees=lambda x: x * 180 / sp.pi, radians=lambda x: x * sp.pi / 180)
    names = {n for n in NAME.findall(expr) if n not in allowed}
    local = {n: sp.Symbol(n) for n in names}

    def P(s):
        return parse_expr(s, local_dict={**allowed, **local}, global_dict={"Integer": sp.Integer, "Float": sp.Float,
                                                                           "Rational": sp.Rational, "Symbol": sp.Symbol},
                          transformations=tr, evaluate=True)
    if expr.count("=") == 1 and not re.search(r"[<>!=]=|=[<>=]", expr):
        lhs, rhs = expr.split("=")
        eq = sp.Eq(P(lhs), P(rhs))
        free = sorted(eq.free_symbols, key=str)
        if not free:
            return {"answer": "true" if bool(eq) else "false", "kind": "check"}
        sol = sp.solve(eq, free[0] if len(free) == 1 else free, dict=True)
        if not sol:
            return {"answer": "no solution", "kind": "solve"}
        parts = [f"{k} = {_show(sp, v)}" for s in sol[:12] for k, v in s.items()]
        return {"answer": "\n".join(parts), "kind": "solve"}
    v = P(expr)
    if hasattr(v, "is_number") and v.is_number:
        return {"answer": _show(sp, v), "kind": "number"}
    return {"answer": _pretty(v), "kind": "algebra"}


def _pretty(v):
    return str(v).replace("**", "^").replace("zoo", "undefined").replace("oo", "∞")


def _approx(c):
    if abs(c.imag) < 1e-12 * max(1.0, abs(c.real)):
        return _num(c.real)
    return f"{_num(c.real)} {'+' if c.imag >= 0 else '-'} {_num(abs(c.imag))}i"


def _show(sp, v):
    """An exact answer with its decimal value: '1 + sqrt(2) ≈ 2.414213562'."""
    if not getattr(v, "is_number", False):
        return _pretty(v)
    if v.has(sp.zoo) or v.has(sp.nan):
        return "undefined (division by zero)"
    if v.is_Integer:
        return str(v)
    try:
        approx = _approx(complex(v.evalf(15)))
    except (TypeError, ValueError):
        return _pretty(v)
    exact = _pretty(sp.nsimplify(v) if v.is_Float else v)
    if v.is_Float or len(exact) > 40 or exact == approx:
        return approx
    return f"{exact} ≈ {approx}"


_OPS = {ast.Add: lambda a, b: a + b, ast.Sub: lambda a, b: a - b, ast.Mult: lambda a, b: a * b,
        ast.Div: lambda a, b: a / b, ast.Pow: lambda a, b: a ** b, ast.Mod: lambda a, b: a % b}
_F = {"sin": math.sin, "cos": math.cos, "tan": math.tan, "asin": math.asin, "acos": math.acos, "atan": math.atan,
      "atan2": math.atan2, "sqrt": math.sqrt, "exp": math.exp, "log": math.log, "log10": math.log10, "log2": math.log2,
      "abs": abs, "sinh": math.sinh, "cosh": math.cosh, "tanh": math.tanh, "floor": math.floor, "ceil": math.ceil,
      "degrees": math.degrees, "radians": math.radians, "factorial": math.factorial}


def _plain(expr):
    """Numbers only, without sympy: a tiny safe calculator."""
    expr = re.sub(r"(\d)\s*([A-Za-z(])", r"\1*\2", expr.replace("^", "**"))
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError:
        raise ValueError("solving and algebra need sympy (run start.bat once with internet)") from None

    def ev(n):
        if isinstance(n, ast.Expression):
            return ev(n.body)
        if isinstance(n, ast.Constant) and isinstance(n.value, (int, float)):
            return n.value
        if isinstance(n, ast.BinOp) and type(n.op) in _OPS:
            a, b = ev(n.left), ev(n.right)
            if isinstance(n.op, ast.Pow) and abs(b) > 1000:
                raise ValueError("that power is too big")
            return _OPS[type(n.op)](a, b)
        if isinstance(n, ast.UnaryOp) and isinstance(n.op, (ast.USub, ast.UAdd)):
            return -ev(n.operand) if isinstance(n.op, ast.USub) else ev(n.operand)
        if isinstance(n, ast.Name) and n.id in ("pi", "e"):
            return math.pi if n.id == "pi" else math.e
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id in _F and not n.keywords:
            return _F[n.func.id](*[ev(a) for a in n.args])
        raise ValueError("without sympy the math pad only does numbers (start.bat installs sympy)")
    return _num(float(ev(tree)))
