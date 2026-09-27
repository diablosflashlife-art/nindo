"""La version de Nindō, et où chercher la suivante.

`VERSION` suit le versionnage sémantique : 1.2.0 → 1.2.1 pour une correction,
1.3.0 pour une nouveauté, 2.0.0 pour ce qui casse les sauvegardes. Publier une
version, c'est pousser l'étiquette `v<VERSION>` sur GitHub : l'intégration
continue fabrique Nindō.exe et le lanceur de chaque joueur le propose.
"""
VERSION = "2.0.2"

# Le dépôt GitHub public d'où viennent les mises à jour, « propriétaire/nom ».
# Vide tant que le dépôt n'existe pas : le lanceur ne cherche alors rien.
DEPOT = "diablosflashlife-art/nindo"

# Le fichier attaché à chaque version publiée.
ARCHIVE = "Nindo-windows.zip"
