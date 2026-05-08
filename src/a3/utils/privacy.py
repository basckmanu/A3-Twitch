# src/a3/utils/privacy.py
#
# Fonctions de pseudonymisation pour A3.
# Tous les identifiants utilisateurs ( usernames, auteur du trigger, reviewers )
# sont hashes avec A3_HASH_SALT avant d'être stockés en DB ou dans les logs.

import hashlib
import os

from dotenv import load_dotenv

load_dotenv()

A3_HASH_SALT = os.getenv("A3_HASH_SALT", "")


def pseudonymize(value: str | None) -> str | None:
    """
    Retourne un hash SHA-256 (16 premiers chars) de value en utilisant A3_HASH_SALT.
    Si value est None ou vide, retourne None.
    """
    if not value:
        return None
    if not A3_HASH_SALT:
        raise RuntimeError("A3_HASH_SALT n'est pas défini dans les variables d'environnement")
    payload = f"{A3_HASH_SALT}:{value}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:16]