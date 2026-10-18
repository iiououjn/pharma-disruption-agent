"""Normalisation of ingredients, strengths, dosage forms and NDCs.

All comparisons in the matcher go through these functions. Strengths are parsed
into numbers + canonical dimension, never compared as strings.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

# ---------------------------------------------------------------- ingredients
SYNONYMS = {
    "salbutamol": "albuterol",
    "paracetamol": "acetaminophen",
    "adrenaline": "epinephrine",
    "noradrenaline": "norepinephrine",
    "frusemide": "furosemide",
}
# Names where the "salt" word IS the ingredient; must not be stripped.
PROTECTED = {
    "sodium chloride", "potassium chloride", "sodium bicarbonate",
    "calcium chloride", "magnesium sulfate", "sodium phosphate",
}
SALTS = {
    "hydrochloride", "hcl", "sodium", "potassium", "calcium", "sulfate",
    "sulphate", "acetate", "phosphate", "citrate", "maleate", "tartrate",
    "mesylate", "succinate", "besylate", "hydrobromide", "bromide", "fumarate",
    "nitrate", "disodium", "dihydrate", "monohydrate", "anhydrous",
}
FORM_WORDS = {
    "injection", "injectable", "tablet", "tablets", "capsule", "capsules",
    "solution", "suspension", "oral", "powder", "for", "extended", "release",
    "extended-release", "film", "coated", "inhalation", "usp", "ophthalmic",
    "topical", "intravenous", "iv", "vial", "premixed", "pf", "er", "xr", "xl",
    "delayed", "delayed-release", "syrup", "in", "single", "dose", "multi",
}


@dataclass(frozen=True)
class Component:
    base: str
    salts: frozenset


def parse_components(name: str | None) -> list[Component]:
    """'Amoxicillin and Clavulanate Potassium' -> [amoxicillin, clavulanate{potassium}]."""
    if not name:
        return []
    s = re.sub(r"\(.*?\)", " ", name.lower())
    chunks = re.split(r"\s+and\s+|;|/|\s+with\s+|\s*\+\s*", s)
    out: list[Component] = []
    for chunk in chunks:
        toks = [t for t in re.findall(r"[a-z][a-z\-]*", chunk) if t not in FORM_WORDS]
        if not toks:
            continue
        joined = " ".join(toks)
        if joined in PROTECTED:
            comp = Component(joined, frozenset())
        else:
            salts = frozenset(t for t in toks if t in SALTS)
            base_toks = [SYNONYMS.get(t, t) for t in toks if t not in SALTS]
            if not base_toks:
                continue
            comp = Component(" ".join(base_toks), salts)
        if all(c.base != comp.base for c in out):
            out.append(comp)
    return out


# ------------------------------------------------------------------ strengths
@dataclass(frozen=True)
class Strength:
    value: float      # canonical: mg, units, meq, or per-mL concentration of those
    dim: str          # 'mg','units','meq','mg/ml','units/ml','meq/ml'


_MASS = {"mg": 1.0, "mcg": 0.001, "ug": 0.001, "µg": 0.001, "g": 1000.0}
_UNITS = {"unit", "units", "iu"}
_PIECE = re.compile(
    r"^\s*(\d[\d,]*\.?\d*|\.\d+)\s*(%|[a-zA-Zµ]+)\s*(?:/\s*(\d[\d,]*\.?\d*|\.\d+)?\s*([a-zA-Z]+))?\s*$"
)


def _num(s: str) -> float:
    return float(s.replace(",", ""))


def parse_strength_piece(piece: str) -> list[Strength] | None:
    """One strength string -> list of Strength (len>1 for 'a mg/b mg' combination tablets).

    Percent is converted assuming w/v (1% = 10 mg/mL). Returns None if unparseable.
    """
    m = _PIECE.match(piece or "")
    if not m:
        return None
    n, unit, dn, dunit = m.group(1), m.group(2).lower(), m.group(3), m.group(4)
    num = _num(n)
    if unit == "%":
        return [Strength(num * 10.0, "mg/ml")] if not dunit else None
    if unit in _MASS:
        amount, base = num * _MASS[unit], "mg"
    elif unit in _UNITS:
        amount, base = num, "units"
    elif unit == "meq":
        amount, base = num, "meq"
    else:
        return None
    if not dunit:
        return [Strength(amount, base)]
    du = dunit.lower()
    if du in ("ml", "l"):
        den = _num(dn) if dn else 1.0
        if du == "l":
            den *= 1000.0
        if den <= 0:
            return None
        return [Strength(amount / den, base + "/ml")]
    if du in _MASS and base == "mg" and dn:
        return [Strength(amount, "mg"), Strength(_num(dn) * _MASS[du], "mg")]
    return None


def parse_strength_pieces(raw) -> list[list[Strength]] | None:
    """raw: str | list[str] | None -> list of parsed pieces, or None if empty / any piece fails."""
    if raw is None:
        return None
    items = [raw] if isinstance(raw, str) else list(raw)
    pieces: list[str] = []
    for it in items:
        pieces += [p for p in str(it).split(";") if p.strip()]
    if not pieces:
        return None
    parsed = [parse_strength_piece(p) for p in pieces]
    if any(p is None for p in parsed):
        return None
    return parsed  # type: ignore[return-value]


def strength_options(n_components: int, pieces: list[list[Strength]] | None):
    """Alternatives, each a tuple of n_components strengths in component order; None if unknowable."""
    if not pieces or n_components < 1:
        return None
    if n_components == 1:
        if any(len(p) != 1 for p in pieces):
            return None
        return [(p[0],) for p in pieces]
    if len(pieces) == n_components and all(len(p) == 1 for p in pieces):
        return [tuple(p[0] for p in pieces)]
    if all(len(p) == n_components for p in pieces):
        return [tuple(p) for p in pieces]
    return None


def strengths_equal(a: Strength, b: Strength, rel_tol: float = 0.01) -> bool:
    return a.dim == b.dim and abs(a.value - b.value) <= rel_tol * max(a.value, b.value, 1e-12)


# ---------------------------------------------------------------- dosage form
@dataclass(frozen=True)
class Form:
    family: str    # tablet|capsule|injection|oral_liquid|inhalation|unspecified|other
    release: str   # immediate|extended|delayed


def parse_form(text: str | None) -> Form | None:
    if not text or not text.strip():
        return None
    t = text.lower()
    if re.search(r"extended[- ]?release|\b(er|xr|xl|sr)\b", t):
        release = "extended"
    elif re.search(r"delayed[- ]?release|\bdr\b|enteric", t):
        release = "delayed"
    else:
        release = "immediate"
    if "inhal" in t:
        fam = "inhalation"
    elif re.search(r"inject|infusion", t):
        fam = "injection"
    elif "tablet" in t:
        fam = "tablet"
    elif "capsule" in t:
        fam = "capsule"
    elif re.search(r"suspension|syrup|elixir|\boral\b|drops", t):
        fam = "oral_liquid"
    elif re.search(r"solution|powder", t):
        fam = "unspecified"      # bare 'Solution' could be oral, injectable, ...
    else:
        fam = "other"
    return Form(fam, release)


# ------------------------------------------------------------------------ NDC
def norm_ndc(s: str | None) -> str | None:
    """11-digit 5-4-2 when hyphenated with 3 segments, 9-digit product level for 2 segments."""
    if not s:
        return None
    parts = [p for p in re.split(r"[-\s]", s.strip()) if p]
    if not parts or not all(p.isdigit() for p in parts):
        return None
    if len(parts) == 3:
        return parts[0].zfill(5) + parts[1].zfill(4) + parts[2].zfill(2)
    if len(parts) == 2:
        return parts[0].zfill(5) + parts[1].zfill(4)
    return "".join(parts)


def ndc_match(a: list[str], b: list[str]) -> bool:
    na = [x for x in (norm_ndc(i) for i in a) if x]
    nb = [x for x in (norm_ndc(i) for i in b) if x]
    return any(x == y or x.startswith(y) or y.startswith(x) for x in na for y in nb)


def norm_text(s: str | None) -> str:
    return re.sub(r"\s+", " ", (s or "").strip().lower())
