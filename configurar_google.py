"""Configuração local do OAuth Google, sem exibir o segredo do cliente."""

from getpass import getpass
import argparse
import json
import os
from pathlib import Path
import secrets
import toml

DEFAULT_REDIRECT = "http://localhost:8502/oauth2callback"
DEFAULT_TARGET = Path(__file__).resolve().parent / ".streamlit" / "secrets.toml"


def read_google_file(source, redirect_uri=DEFAULT_REDIRECT):
    try:
        data = json.loads(Path(source).read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as exc:
        raise ValueError("Não foi possível ler o arquivo JSON de credenciais.") from exc
    web = data.get("web") if isinstance(data, dict) else None
    if not isinstance(web, dict):
        raise ValueError("Use o JSON de um cliente OAuth do tipo Aplicativo da Web.")
    client_id, client_secret = web.get("client_id"), web.get("client_secret")
    if not isinstance(client_id, str) or not client_id.strip() or not isinstance(client_secret, str) or not client_secret.strip():
        raise ValueError("O JSON não contém Client ID e Client secret válidos.")
    if redirect_uri not in web.get("redirect_uris", []):
        raise ValueError(f"Cadastre {redirect_uri} nas URIs de redirecionamento do cliente Google e baixe o JSON atualizado.")
    return client_id.strip(), client_secret.strip()


def save_configuration(client_id, client_secret, redirect_uri=DEFAULT_REDIRECT, target=DEFAULT_TARGET):
    if not isinstance(client_id, str) or not client_id.strip() or not isinstance(client_secret, str) or not client_secret.strip():
        raise ValueError("Client ID e client secret são obrigatórios. Nenhum arquivo foi alterado.")
    target = Path(target)
    previous = toml.loads(target.read_text(encoding="utf-8")) if target.exists() else {}
    auth = previous.setdefault("auth", {})
    auth["redirect_uri"] = redirect_uri
    if not isinstance(auth.get("cookie_secret"), str) or len(auth["cookie_secret"]) < 32:
        auth["cookie_secret"] = secrets.token_urlsafe(48)
    auth["google"] = {
        "client_id": client_id.strip(),
        "client_secret": client_secret.strip(),
        "server_metadata_url": "https://accounts.google.com/.well-known/openid-configuration",
    }
    target.parent.mkdir(parents=True, exist_ok=True)
    staging = target.with_suffix(".tmp")
    staging.write_text(toml.dumps(previous), encoding="utf-8")
    os.replace(staging, target)
    return target


def main():
    parser = argparse.ArgumentParser(description="Configurar login Google sem exibir os segredos.")
    parser.add_argument("--arquivo", type=Path, help="JSON do cliente OAuth Web baixado do Google Cloud")
    parser.add_argument("--redirect-uri", default=DEFAULT_REDIRECT)
    args = parser.parse_args()
    try:
        if args.arquivo:
            client_id, client_secret = read_google_file(args.arquivo, args.redirect_uri)
            redirect_uri = args.redirect_uri
        else:
            client_id = input("Client ID OAuth do Google: ").strip()
            client_secret = getpass("Client secret OAuth do Google (oculto): ").strip()
            redirect_uri = input(f"Redirect URI [{args.redirect_uri}]: ").strip() or args.redirect_uri
        save_configuration(client_id, client_secret, redirect_uri)
    except (ValueError, OSError) as exc:
        raise SystemExit(str(exc)) from None
    print("Login Google configurado. Reinicie o Streamlit para aplicar.")


if __name__ == "__main__":
    main()
