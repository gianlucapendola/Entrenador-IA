"""
Conexión con WHOOP.

Uso:
    python whoop_auth.py          -> permiso inicial (una sola vez)
    from whoop_auth import api_get
    api_get("/recovery", limit=7) -> trae datos

Antes de correrlo, poné tu client id y secret en un archivo .env
"""

import json
import os
import secrets
import time
import urllib.parse
import webbrowser
from pathlib import Path

import requests

from dotenv import load_dotenv
load_dotenv()

AUTH_URL = "https://api.prod.whoop.com/oauth/oauth2/auth"
TOKEN_URL = "https://api.prod.whoop.com/oauth/oauth2/token"
API_BASE = "https://api.prod.whoop.com/developer/v2"

# Tiene que ser IDENTICO al que registraste en el dashboard de WHOOP.
# Ojo: https (no http). Esa pagina no existe de verdad, y esta bien:
# solo sirve para que WHOOP nos devuelva el codigo en la barra del navegador.
REDIRECT_URI = "https://localhost:8765/callback"

# Cuidado con la escritura: cycles va en plural, workout y sleep en singular.
SCOPES = [
    "read:recovery",
    "read:cycles",
    "read:sleep",
    "read:workout",
    "read:body_measurement",
    "offline",  # sin esto no hay llave de repuesto
]

TOKEN_FILE = Path.home() / ".whoop" / "tokens.json"

CLIENT_ID = os.environ["WHOOP_CLIENT_ID"]
CLIENT_SECRET = os.environ["WHOOP_CLIENT_SECRET"]


def _guardar(payload):
    """Guarda las llaves. Sobrescribe SIEMPRE: la llave de repuesto se renueva
    cada vez que se usa y la anterior queda muerta."""
    data = {
        "access_token": payload["access_token"],
        "refresh_token": payload["refresh_token"],
        "expires_at": time.time() + payload["expires_in"] - 60,
    }
    TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    TOKEN_FILE.write_text(json.dumps(data, indent=2))
    TOKEN_FILE.chmod(0o600)
    return data


def _cargar():
    if not TOKEN_FILE.exists():
        raise RuntimeError("No hay llaves guardadas. Corre: python whoop_auth.py")
    return json.loads(TOKEN_FILE.read_text())


def autorizar():
    """Permiso inicial. Se hace una sola vez."""
    state = secrets.token_urlsafe(16)

    params = {
        "client_id": CLIENT_ID,
        "redirect_uri": REDIRECT_URI,
        "response_type": "code",
        "scope": " ".join(SCOPES),
        "state": state,
    }
    url = f"{AUTH_URL}?{urllib.parse.urlencode(params)}"

    print("\nSe va a abrir el navegador. Dale permiso a la app.")
    print("Despues vas a ver una PAGINA DE ERROR. Eso es normal y esperado.")
    print("Copia la direccion COMPLETA de la barra de arriba y pegala aca abajo.\n")
    print(f"Si el navegador no abre solo, entra aca:\n{url}\n")
    webbrowser.open(url)

    pegado = input("Pega la direccion completa y dale Enter:\n> ").strip()

    query = urllib.parse.urlparse(pegado).query
    devuelto = dict(urllib.parse.parse_qsl(query))

    if "error" in devuelto:
        raise RuntimeError(f"WHOOP devolvio un error: {devuelto}")
    if not devuelto.get("code"):
        raise RuntimeError("No encontre el codigo en esa direccion. Pegala completa.")
    if devuelto.get("state") != state:
        raise RuntimeError("El codigo no corresponde a esta sesion. Volve a empezar.")

    r = requests.post(
        TOKEN_URL,
        data={
            "grant_type": "authorization_code",
            "code": devuelto["code"],
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
            "redirect_uri": REDIRECT_URI,
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=30,
    )
    r.raise_for_status()
    payload = r.json()

    if "refresh_token" not in payload:
        raise RuntimeError(
            "No vino la llave de repuesto. Revisa que 'offline' este marcado "
            "en el dashboard de WHOOP y en la lista SCOPES de arriba."
        )

    _guardar(payload)
    print(f"\nListo. Llaves guardadas en {TOKEN_FILE}")


def _renovar(refresh_token):
    r = requests.post(
        TOKEN_URL,
        data={
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
            "scope": "offline",  # sin esto no viene la llave de repuesto nueva
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=30,
    )
    r.raise_for_status()
    return _guardar(r.json())


def get_access_token():
    """Devuelve una llave valida, renovandola sola si ya vencio."""
    llaves = _cargar()
    if time.time() >= llaves["expires_at"]:
        llaves = _renovar(llaves["refresh_token"])
    return llaves["access_token"]


def api_get(path, **params):
    """Pide datos a WHOOP. Ejemplo: api_get('/recovery', limit=7)

    Rutas principales:
        /cycle                  esfuerzo del dia
        /recovery               recuperacion, HRV, pulso en reposo
        /activity/sleep         sueno
        /activity/workout       entrenamientos
        /user/measurement/body  peso, altura, pulso maximo
    """
    def _pedir():
        return requests.get(
            f"{API_BASE}{path}",
            headers={"Authorization": f"Bearer {get_access_token()}"},
            params=params,
            timeout=30,
        )

    r = _pedir()
    if r.status_code == 401:
        _renovar(_cargar()["refresh_token"])
        r = _pedir()
    r.raise_for_status()
    return r.json()


if __name__ == "__main__":
    autorizar()
    print("\nProbando la conexion...")
    datos = api_get("/recovery", limit=3)
    for reg in datos.get("records", []):
        s = reg.get("score") or {}
        print(
            f"{reg.get('created_at', '?')[:10]}  "
            f"recuperacion={s.get('recovery_score')}  "
            f"hrv={s.get('hrv_rmssd_milli')}  "
            f"pulso_reposo={s.get('resting_heart_rate')}"
        )
