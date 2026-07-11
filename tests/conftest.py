# tests/conftest.py
#
# Les tests ne doivent pas dépendre d'un .env réel : privacy.py lit A3_HASH_SALT au
# chargement du module et lève une RuntimeError si absent (voir Brain.analyze() qui
# appelle pseudonymize() dès qu'un clip se déclenche). Sans ce fichier, les tests
# passent en local par accident (un vrai .env existe sur la machine de dev) mais
# échouent en CI où aucun .env n'est commité. Doit s'exécuter avant l'import de
# a3.Twitch.Brain.mainBrainTwitch par les modules de test.

import os

os.environ.setdefault("A3_HASH_SALT", "test-salt-jamais-utilise-en-prod")
