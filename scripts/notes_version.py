"""Prépare une version pour GitHub : vérifie l'étiquette, écrit les notes.

Appelé par .github/workflows/publier.yml avec l'étiquette poussée (v1.2.0).
Refuse de publier si l'étiquette ne correspond pas à app/version.py : un
lanceur qui annoncerait 1.2.0 en étant 1.1.0 proposerait la mise à jour en
boucle.
"""
import os
import re
import sys
from pathlib import Path

RACINE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RACINE))

from app.version import VERSION  # noqa: E402


def section(version: str) -> tuple[str, str]:
    texte = (RACINE / "NOUVEAUTES.md").read_text(encoding="utf-8")
    m = re.search(rf"^##\s+{re.escape(version)}\s*(?:—|-)\s*([^\n]*)\n(.*?)(?=^##\s|\Z)",
                  texte, re.M | re.S)
    if not m:
        return "", ""
    return m.group(1).strip(), m.group(2).strip()


def main() -> int:
    etiquette = (sys.argv[1] if len(sys.argv) > 1 else "").lstrip("v")
    if etiquette != VERSION:
        print(f"L'étiquette v{etiquette} ne correspond pas à app/version.py ({VERSION}).",
              file=sys.stderr)
        return 1
    nom, corps = section(VERSION)
    if not corps:
        print(f"Aucune section « ## {VERSION} — … » dans NOUVEAUTES.md.", file=sys.stderr)
        return 1
    (RACINE / "notes.md").write_text(corps + "\n", encoding="utf-8")
    sortie = os.environ.get("GITHUB_OUTPUT")
    if sortie:
        with open(sortie, "a", encoding="utf-8") as f:
            f.write(f"titre={VERSION} — {nom}\n")
    print(f"Nindō {VERSION} — {nom}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
