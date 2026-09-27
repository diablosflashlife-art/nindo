# -*- mode: python ; coding: utf-8 -*-
# Recette de Nindō.exe. Fabriquer : pyinstaller nindo.spec  →  dist/Nindo/
#
# Ce qui part dans l'application : le code, les gabarits, le lore, les règles
# et le journal des nouveautés. Ce qui n'y part JAMAIS : .env (ta clé), data/
# (tes parties), .venv. Les données de chaque joueur vivent dans
# %APPDATA%\Nindo, voir app/config.py.
from PyInstaller.utils.hooks import collect_submodules

donnees = [
    ("app/web", "app/web"),
    ("lore", "lore"),
    ("rulesets", "rulesets"),
    ("NOUVEAUTES.md", "."),
]

caches = (collect_submodules("app")
          + collect_submodules("uvicorn")
          + ["webview", "clr"])

a = Analysis(
    ["nindo.py"],
    pathex=[],
    binaries=[],
    datas=donnees,
    hiddenimports=caches,
    excludes=["pytest", "tkinter"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Nindo",
    icon="app/web/static/nindo.ico",
    console=False,
    upx=False,
)
coll = COLLECT(exe, a.binaries, a.datas, name="Nindo", upx=False)
