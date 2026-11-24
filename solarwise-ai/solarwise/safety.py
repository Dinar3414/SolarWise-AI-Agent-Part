"""Safety rails (C5): refusal policy, prompt-injection isolation, call limits."""
from __future__ import annotations
import re

REFUSAL = ("I can't verify this value from the supplied documents. Please provide the exact model "
           "datasheet or ask a qualified engineer.")

_APPROVE = re.compile(r"\b(approve|approved|certif(y|ied)|sign[- ]?off|guarantee[sd]?|code[- ]compliant|"
                      r"safe to install|ready to install|stamp)\b", re.I)
_BYPASS = re.compile(r"\b(bypass|skip|remove|without|disable|ignore)\b.{0,30}\b(breaker|mcb|fuse|isolator|spd|"
                     r"earth(ing)?|ground(ing)?|rcd|protection|safety)\b", re.I)
_INJECT = re.compile(r"(ignore (all |any )?(previous|prior|above) (instructions|rules)|disregard .{0,30}instructions|"
                     r"you are now|act as|system prompt|reveal .{0,20}(prompt|key)|new instructions?:|"
                     r"<\s*/?\s*(system|assistant)\s*>)", re.I)
_COMPAT = re.compile(r"\b(compatib|will (it|this) (work|fit)|can i connect|is (it|this) ok(ay)? with|string .*(ok|safe|pass))", re.I)


def classify_risk(text: str) -> dict:
    """Return {'refuse': bool, 'reason': str}. Compatibility *questions* are allowed (the check tool decides)."""
    if _BYPASS.search(text):
        return {"refuse": True, "reason": "I can't advise bypassing, removing or skipping protection or safety devices "
                                          "(breakers, fuses, isolators, SPDs, earthing). Please consult a qualified engineer."}
    if _APPROVE.search(text):
        return {"refuse": True, "reason": "I can't approve, certify or guarantee a design or its code compliance. "
                                          "SolarWise AI gives preliminary calculations only; a qualified engineer must review and approve."}
    return {"refuse": False, "reason": ""}


def is_compat_request(text: str) -> bool:
    return bool(_COMPAT.search(text))


def contains_injection(text: str) -> bool:
    return bool(_INJECT.search(text))


def sanitize_evidence(text: str) -> tuple[str, bool]:
    """Retrieved PDF text is untrusted evidence: strip instruction-like lines, flag if any."""
    flagged, keep = False, []
    for ln in text.splitlines():
        if _INJECT.search(ln):
            flagged = True
            continue
        keep.append(ln)
    return "\n".join(keep), flagged


def wrap_untrusted(text: str) -> str:
    return f"<untrusted_evidence>\n{text}\n</untrusted_evidence>"
