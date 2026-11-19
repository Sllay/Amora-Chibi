#!/usr/bin/env python3
"""Verifica o snapshot e publica apenas ZIP WinZip AES-256 no artifact.

Nunca imprime senhas, tokens, conteudo das tabelas nem hashes privados.
"""
from __future__ import annotations

import hashlib
import os
import sqlite3
import zipfile
from pathlib import Path

import pyzipper

ROOT = Path('/tmp/amora-export-ci')
SRC = ROOT / 'source'
OUT = ROOT / 'output' / 'amora-backup.zip'
FILES = (
    SRC / 'amora-pre-pcs4c.db',
    SRC / 'amora-pre-pcs4c.db.avatar.zip',
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def validate_source() -> None:
    for path in FILES:
        if not path.is_file() or path.stat().st_size == 0:
            raise RuntimeError(f'Arquivo ausente ou vazio: {path.name}')

    with sqlite3.connect(FILES[0].as_uri() + '?mode=ro&immutable=1', uri=True) as db:
        result = db.execute('PRAGMA quick_check').fetchone()
        if not result or result[0] != 'ok':
            raise RuntimeError('Integridade do banco SQLite nao confirmada')

    with zipfile.ZipFile(FILES[1]) as archive:
        if archive.testzip() is not None:
            raise RuntimeError('Arquivo de assets ZIP invalido')

    print('Integridade SQLite e assets ZIP confirmada.')


def encrypt_and_verify() -> None:
    secret = os.environ.get('AMORA_BACKUP_PASSPHRASE', '')
    if len(secret) < 32:
        raise RuntimeError('AMORA_BACKUP_PASSPHRASE deve ter pelo menos 32 caracteres')
    password = secret.encode('utf-8')
    original_digests = {p.name: sha256_file(p) for p in FILES}

    OUT.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with pyzipper.AESZipFile(
        OUT, 'w', compression=zipfile.ZIP_DEFLATED, allowZip64=True
    ) as archive:
        archive.setpassword(password)
        archive.setencryption(pyzipper.WZ_AES, nbits=256)
        for path in FILES:
            archive.write(path, arcname=path.name)

    with pyzipper.AESZipFile(OUT, 'r') as archive:
        archive.setpassword(password)
        if set(archive.namelist()) != set(original_digests):
            raise RuntimeError('Arquivos internos inesperados no ZIP criptografado')
        for name, original_digest in original_digests.items():
            info = archive.getinfo(name)
            if not (info.flag_bits & 1) or info.compress_type != 99:
                raise RuntimeError('Arquivo nao protegido com ZIP AES')
            verified_digest = hashlib.sha256()
            with archive.open(name) as stream:
                for block in iter(lambda: stream.read(1024 * 1024), b''):
                    verified_digest.update(block)
            if verified_digest.hexdigest() != original_digest:
                raise RuntimeError('Hash divergente apos descriptografia')

    print('ZIP AES-256 validado por descriptografia e SHA-256 de ambos os arquivos.')
    print('Archive pronto para upload; origem nunca sera publicada como artifact.')


if __name__ == '__main__':
    validate_source()
    encrypt_and_verify()
