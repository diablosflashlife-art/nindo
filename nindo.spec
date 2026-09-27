# -*- mode: python ; coding: utf-8 -*-
# Recette de Nindō.exe. Fabriquer : pyinstaller nindo.spec  →  dist/Nindo/
#
# Ce qui part dans l'application : le code, les gabarits, le lore, les règles
# et le journal des nouveautés. Ce qui n'y part JAMAIS : .env (ta clé), data/
# (tes parties), .venv. Les données de chaque joueur vivent dans
# %APPDATA%\Nindo, voir app/config.py.
#
# La voix réaliste (Piper) : le moteur et les données d'espeak-ng partent avec
# l'application ; la voix elle-même (≈ 60 Mo) se télécharge depuis le lanceur.
from PyInstaller.utils.hooks import (collect_data_files, collect_dynamic_libs,
                                     collect_submodules)

donnees = [
    ("app/web", "app/web"),
    ("lore", "lore"),
    ("rulesets", "rulesets"),
    ("NOUVEAUTES.md", "."),
] + collect_data_files("piper") + collect_data_files("onnxruntime")

caches = (collect_submodules("app")
          + collect_submodules("uvicorn")
          + ["piper", "piper.voice", "piper.config", "piper.download_voices",
             "piper.phonemize_espeak", "piper.espeakbridge", "onnxruntime"]
          + ["webview", "clr"])

a = Analysis(
    ["nindo.py"],
    pathex=[],
    binaries=collect_dynamic_libs("piper") + collect_dynamic_libs("onnxruntime"),
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
