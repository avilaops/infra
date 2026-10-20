#!/usr/bin/env python3
"""Emite credenciais R2 temporárias restritas ao bucket fiscal, sem rede.

O token pai continua exclusivamente em /root/.config/rclone/rclone.conf;
o app Lojas recebe somente uma credencial temporária de objetos fiscal/v1.
"""
import base64
import configparser
import hashlib
import hmac
import json
import os
import pathlib
import tempfile
import time

CONTA = "85b914dcdebf6fa2fce7b819bc542294"
BUCKET = "avilaops-fiscal-acervo"
DESTINO = pathlib.Path("/opt/lojas/r2-secrets")

def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")

def emitir(id_pai: str, segredo_pai: str, agora: int, duracao: int = 172800) -> dict:
    aud = f"{CONTA}.r2.cloudflarestorage.com"
    header = {"alg": "HS256", "typ": "JWT"}
    claims = {
        "bucket": BUCKET,
        "actions": ["GetObject", "PutObject", "HeadObject"],
        "paths": {"prefixPaths": ["v1/"], "objectPaths": []},
        "sub": CONTA, "iss": id_pai, "aud": aud, "iat": agora, "exp": agora + duracao,
    }
    cab = b64url(json.dumps(header, separators=(",", ":")).encode())
    payload = b64url(json.dumps(claims, separators=(",", ":")).encode())
    unsigned = f"{cab}.{payload}"
    assinatura = b64url(hmac.new(segredo_pai.encode(), unsigned.encode(), hashlib.sha256).digest())
    jwt = f"{unsigned}.{assinatura}"
    return {
        "accessKeyId": id_pai,
        "secretAccessKey": hashlib.sha256(jwt.encode()).hexdigest(),
        "sessionToken": base64.b64encode(f"jwt/{jwt}".encode()).decode(),
        "expiresAt": (agora + duracao) * 1000,
    }

def main():
    if os.geteuid() != 0:
        raise SystemExit("Execute a rotação como root.")
    config = configparser.ConfigParser(interpolation=None)
    arquivos = config.read("/root/.config/rclone/rclone.conf")
    if not arquivos or not config.has_section("r2"):
        raise SystemExit("A credencial pai do R2 não está configurada.")
    id_pai = config.get("r2", "access_key_id")
    segredo_pai = config.get("r2", "secret_access_key")
    if not id_pai or not segredo_pai:
        raise SystemExit("Credencial pai incompleta.")
    DESTINO.mkdir(mode=0o750, parents=True, exist_ok=True)
    os.chown(DESTINO, 0, 1000)
    os.chmod(DESTINO, 0o750)
    resultado = emitir(id_pai, segredo_pai, int(time.time()))
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=DESTINO, prefix=".cred-", delete=False) as f:
        json.dump(resultado, f, separators=(",", ":"))
        f.flush()
        os.fsync(f.fileno())
        arquivo = pathlib.Path(f.name)
    os.chown(arquivo, 0, 1000)
    os.chmod(arquivo, 0o640)
    os.replace(arquivo, DESTINO / "credentials.json")
    print("Credencial fiscal temporária renovada para o bucket privado.")

if __name__ == "__main__":
    main()
