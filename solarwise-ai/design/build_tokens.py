"""tokens.json -> tokens.css (CSS variables, three layers). Run: python design/build_tokens.py
Also validates that app code uses no hard-coded hex colours (token compliance)."""
import json, re, sys
from pathlib import Path
HERE = Path(__file__).parent
tok = json.loads((HERE / "tokens.json").read_text())


def flat(d, prefix=""):
    for k, v in d.items():
        if isinstance(v, dict):
            yield from flat(v, f"{prefix}{k}-")
        else:
            yield f"{prefix}{k}", v


def css_name(layer, path):
    return f"--sw-{layer}-{path}".replace(".", "-")


def resolve(v):
    return re.sub(r"\{(\w+)\.([\w.\-]+)\}", lambda m: f"var({css_name(m.group(1), m.group(2))})", v)


def build() -> str:
    lines = [":root {"]
    for layer in ("primitive", "semantic", "component"):
        lines.append(f"  /* {layer} */")
        for name, val in flat(tok[layer]):
            lines.append(f"  {css_name(layer, name)}: {resolve(val)};")
    lines.append("}")
    return "\n".join(lines)


def check_hardcoded(paths) -> list[str]:
    bad = []
    for p in paths:
        for i, ln in enumerate(Path(p).read_text().splitlines(), 1):
            if re.search(r"#[0-9a-fA-F]{6}\b", ln) and "tokens" not in ln:
                bad.append(f"{p}:{i}: {ln.strip()[:80]}")
    return bad


if __name__ == "__main__":
    (HERE / "tokens.css").write_text(build() + "\n")
    print("wrote design/tokens.css")
    if "--check" in sys.argv:
        issues = check_hardcoded(sys.argv[sys.argv.index("--check") + 1:])
        print("\n".join(issues) or "token compliance: OK")
        sys.exit(1 if issues else 0)
