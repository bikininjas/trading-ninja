#!/usr/bin/env python3
"""Génère `docs/CODE_INDEX.md` à partir du code réel.

L'index est **calculé**, jamais écrit à la main : un index manuel dérive au
premier commit et devient un mensonge poli. Il est donc régénérable et
vérifiable :

    python scripts/index_codebase.py            # écrit docs/CODE_INDEX.md
    python scripts/index_codebase.py --check    # sort 1 si l'index est périmé
    python scripts/index_codebase.py --stdout   # affiche sans écrire

Ce qu'il indexe : les modules Python (classes, méthodes, fonctions, lignes), les
tests (nombre par fichier), les migrations SQL (tables, vues, types créés) et les
scripts. Aucune dépendance externe : `ast` et `re` suffisent.

Le fichier produit ne contient ni horodatage ni numéro de commit — seulement une
empreinte SHA-256 des entrées. Deux exécutions sur le même code produisent donc
exactement le même octet, ce qui rend `--check` utilisable en intégration.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "docs" / "CODE_INDEX.md"
PY_ROOTS = (ROOT / "backend" / "app", ROOT / "backend" / "scripts", ROOT / "backend" / "tests")
SQL_DIR = ROOT / "backend" / "db" / "migrations"
SH_DIR = ROOT / "scripts"

CREATE_TABLE = re.compile(r"CREATE\s+TABLE(?:\s+IF\s+NOT\s+EXISTS)?\s+([a-z0-9_]+)", re.I)
CREATE_VIEW = re.compile(r"CREATE\s+(?:OR\s+REPLACE\s+)?VIEW\s+([a-z0-9_]+)", re.I)
CREATE_TYPE = re.compile(r"CREATE\s+TYPE\s+([a-z0-9_]+)\s+AS\s+ENUM\s*\((.*?)\)", re.I | re.S)
ADD_VALUE = re.compile(r"ALTER\s+TYPE\s+([a-z0-9_]+)\s+ADD\s+VALUE(?:\s+IF\s+NOT\s+EXISTS)?\s+'([^']+)'", re.I)


def first_line(text: str | None) -> str:
    if not text:
        return ""
    for line in text.strip().splitlines():
        line = line.strip()
        if line:
            return line
    return ""


def py_files() -> list[Path]:
    files: list[Path] = []
    for base in PY_ROOTS:
        if base.is_dir():
            files.extend(p for p in base.rglob("*.py") if "__pycache__" not in p.parts)
    return sorted(files)


def render_signature(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    args = ast.unparse(node.args)
    ret = f" -> {ast.unparse(node.returns)}" if node.returns else ""
    prefix = "async def" if isinstance(node, ast.AsyncFunctionDef) else "def"
    return f"{prefix} {node.name}({args}){ret}"


def index_module(path: Path) -> dict:
    rel = path.relative_to(ROOT)
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(rel))
    entry: dict = {
        "path": str(rel),
        "lines": source.count("\n") + (0 if source.endswith("\n") else 1),
        "doc": first_line(ast.get_docstring(tree)),
        "classes": [],
        "functions": [],
    }
    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            methods = [
                (child.name, render_signature(child), child.lineno, first_line(ast.get_docstring(child)))
                for child in node.body
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
            ]
            bases = f"({', '.join(ast.unparse(b) for b in node.bases)})" if node.bases else ""
            entry["classes"].append(
                {
                    "name": node.name + bases,
                    "lineno": node.lineno,
                    "doc": first_line(ast.get_docstring(node)),
                    "methods": methods,
                }
            )
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            entry["functions"].append(
                {
                    "name": render_signature(node),
                    "lineno": node.lineno,
                    "doc": first_line(ast.get_docstring(node)),
                }
            )
    return entry


def count_tests(module: dict) -> int:
    """Nombre de fonctions `test_*` — plancher, pas vérité : `parametrize` multiplie."""
    total = 0
    for fn in module["functions"]:
        name = fn["name"].split("(")[0].replace("async def ", "").replace("def ", "")
        if name.startswith("test"):
            total += 1
    return total


def collected_per_file() -> dict[str, int]:
    """Cas réellement collectés par pytest, fichier par fichier.

    Un compteur statique ignore `parametrize` (73 fonctions → 167 cas ici). Un
    index qui annonce 73 quand la suite en exécute 167 est un index faux, donc
    on demande la mesure à pytest. Si pytest n'est pas disponible, l'appelant
    retombe sur le comptage statique, annoncé comme tel.
    """
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "pytest", "--collect-only", "-q", "-p", "no:cacheprovider"],
            cwd=ROOT / "backend",
            capture_output=True,
            text=True,
            timeout=180,
        )
    except (OSError, subprocess.SubprocessError):
        return {}
    counts: dict[str, int] = {}
    for line in proc.stdout.splitlines():
        match = re.match(r"^(tests/[a-z0-9_]+\.py): (\d+)$", line.strip())
        if match:
            counts[match.group(1)] = int(match.group(2))
    return counts


def index_sql(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    return {
        "path": str(path.relative_to(ROOT)),
        "lines": text.count("\n") + (0 if text.endswith("\n") else 1),
        "tables": CREATE_TABLE.findall(text),
        "views": CREATE_VIEW.findall(text),
        "types": [(name, [v.strip().strip("'") for v in labels.split(",") if v.strip()]) for name, labels in CREATE_TYPE.findall(text)],
        "added_values": ADD_VALUE.findall(text),
    }


def index_shell(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    comments = (
        line.lstrip("#").strip()
        for line in text.splitlines()
        if line.startswith("#") and not line.startswith("#!")
    )
    return {
        "path": str(path.relative_to(ROOT)),
        "lines": text.count("\n") + (0 if text.endswith("\n") else 1),
        "doc": first_line("\n".join(comments)),
    }


def build() -> str:
    modules = [index_module(p) for p in py_files()]
    sqls = [index_sql(p) for p in sorted(SQL_DIR.glob("*.sql"))]
    shells = [index_shell(p) for p in sorted(SH_DIR.glob("*.sh"))]
    tests = [m for m in modules if m["path"].startswith("backend/tests/")]
    total_tests = sum(count_tests(m) for m in tests)
    collected = collected_per_file()
    total_collected = sum(collected.values()) if collected else total_tests
    total_classes = sum(len(m["classes"]) for m in modules)
    total_functions = sum(len(m["functions"]) for m in modules)
    total_lines = sum(m["lines"] for m in modules)

    digest = hashlib.sha256()
    for item in modules:
        digest.update(Path(ROOT / item["path"]).read_bytes())
    for item in sqls:
        digest.update(Path(ROOT / item["path"]).read_bytes())
    for item in shells:
        digest.update(Path(ROOT / item["path"]).read_bytes())

    out: list[str] = []
    add = out.append
    add("# Index du code — Trading Ninja")
    add("")
    add("> Fichier **généré** par `scripts/index_codebase.py`. Ne pas éditer à la main :")
    add("> lancer le script. `bash scripts/check.sh` refuse un index périmé.")
    add("")
    add(f"Empreinte des sources : `{digest.hexdigest()[:16]}`")
    add("")
    add("## 1. Vue d'ensemble")
    add("")
    add("| Mesure | Valeur |")
    add("|---|---|")
    add(f"| Modules Python | {len(modules)} |")
    add(f"| Lignes Python | {total_lines} |")
    add(f"| Classes | {total_classes} |")
    add(f"| Fonctions et méthodes | {total_functions} |")
    add(f"| Cas de test collectés par pytest | {total_collected} |")
    add(f"| Fonctions de test (comptage statique) | {total_tests} |")
    add(f"| Migrations SQL | {len(sqls)} |")
    add(f"| Scripts shell | {len(shells)} |")
    add("")
    add("## 2. Carte des fichiers")
    add("")
    add("| Fichier | Lignes | Rôle (première ligne de docstring) |")
    add("|---|---|---|")
    for item in modules + shells:
        add(f"| `{item['path']}` | {item['lines']} | {item.get('doc', '')} |")
    for item in sqls:
        add(f"| `{item['path']}` | {item['lines']} | migration SQL |")
    add("")
    add("## 3. API interne, module par module")
    add("")
    add("Chaque entrée donne la ligne de définition : c'est le point d'entrée pour lire")
    add("le code, pas une copie de celui-ci.")
    add("")
    for module in modules:
        if not module["classes"] and not module["functions"]:
            continue
        add(f"### `{module['path']}`")
        add("")
        if module["doc"]:
            add(f"*{module['doc']}*")
            add("")
        for cls in module["classes"]:
            doc = f" — {cls['doc']}" if cls["doc"] else ""
            add(f"- **class {cls['name']}** (l.{cls['lineno']}){doc}")
            for name, signature, lineno, method_doc in cls["methods"]:
                suffix = f" — {method_doc}" if method_doc else ""
                add(f"  - `{signature}` (l.{lineno}){suffix}")
        for fn in module["functions"]:
            doc = f" — {fn['doc']}" if fn["doc"] else ""
            add(f"- `{fn['name']}` (l.{fn['lineno']}){doc}")
        add("")
    add("## 4. Tests")
    add("")
    if collected:
        add("Cas réellement collectés par pytest (les `parametrize` sont comptés à leur"
            " juste nombre, pas comme une seule fonction).")
    else:
        add("pytest indisponible au moment de la génération : comptage statique des"
            " fonctions `test_*`, qui sous-estime les cas paramétrés.")
    add("")
    add("| Fichier | Cas collectés |")
    add("|---|---|")
    for module in tests:
        key = module["path"].removeprefix("backend/")
        add(f"| `{module['path']}` | {collected.get(key, count_tests(module))} |")
    add(f"| **Total** | **{total_collected}** |")
    add("")
    add("## 5. Schéma SQL")
    add("")
    for item in sqls:
        add(f"### `{item['path']}`")
        add("")
        if item["tables"]:
            add(f"- Tables : {', '.join(f'`{t}`' for t in item['tables'])}")
        if item["views"]:
            add(f"- Vues : {', '.join(f'`{v}`' for v in item['views'])}")
        for name, labels in item["types"]:
            add(f"- Type `{name}` : {', '.join(f'`{v}`' for v in labels)}")
        for name, value in item["added_values"]:
            add(f"- `ALTER TYPE {name}` ajoute `{value}`")
        if not (item["tables"] or item["views"] or item["types"] or item["added_values"]):
            add("- Aucun objet créé (droits, index ou contraintes).")
        add("")
    add("## 6. Rafraîchir")
    add("")
    add("```bash")
    add("python scripts/index_codebase.py          # régénère ce fichier")
    add("python scripts/index_codebase.py --check  # échoue si périmé")
    add("```")
    add("")
    return "\n".join(out)


def main() -> int:
    parser = argparse.ArgumentParser(description="Index du code, généré depuis les sources")
    parser.add_argument("--check", action="store_true", help="échoue si l'index est périmé")
    parser.add_argument("--stdout", action="store_true", help="affiche sans écrire")
    args = parser.parse_args()

    content = build()
    if args.stdout:
        print(content)
        return 0
    if args.check:
        current = OUTPUT.read_text(encoding="utf-8") if OUTPUT.is_file() else ""
        if current != content:
            print("docs/CODE_INDEX.md est périmé — lancer : python scripts/index_codebase.py", file=sys.stderr)
            return 1
        print("docs/CODE_INDEX.md est à jour")
        return 0
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(content, encoding="utf-8")
    print(f"écrit : {OUTPUT.relative_to(ROOT)} ({len(content.splitlines())} lignes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
