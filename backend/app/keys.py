"""The trading wallet's key — and an honest account of what holding one costs.

This is the piece that makes automatic trading possible, and it is the piece
that changes what this business is. A browser wallet only signs when somebody
is there to press a button; auto-buy fires at three in the morning. So either
nothing trades unattended, or a key lives on a server. There is no third
answer, and pretending otherwise would be the dishonest part.

What that means plainly: from the moment a key is stored here, the operator is
holding customer funds. A break-in stops being an embarrassment and becomes a
theft, whoever was at fault. Every decision below is shaped by trying to make
the blast radius small rather than by trying to make the risk sound small.

  The ciphertext is useless on its own. Keys are sealed with a master key that
  lives in the environment and never in Mongo, so a database dump — the most
  common way this goes wrong — yields nothing but noise. Losing the master key
  means losing every stored wallet, which is the correct trade: a master key
  kept somewhere convenient enough to recover is kept somewhere an attacker
  can reach.

  Nothing reads a key back out. There is no export route, no admin view, no
  log line, and no API field that carries one. The plaintext exists inside one
  function, for as long as it takes to sign, and is never returned upward.

  Making a wallet is offered before importing one. An imported key is usually
  somebody's main wallet, holding everything they own; a created one holds
  exactly what they chose to put in it. Both are supported because people will
  do both, but the default is the one that bounds the loss.

  A key is never accepted over a plain connection. Typed into an http:// page
  it crosses the network in the clear, and every hop between can read it. That
  check lives in the router rather than here, because it is a fact about the
  request, not about the key.
"""

from __future__ import annotations

import base64
import time
from datetime import datetime, timezone
from typing import Optional

from . import db
from .config import settings
from .scanners.slog import get_logger

log = get_logger(__name__)

EVM, SOL, TRON = "evm", "sol", "tron"
KINDS = (EVM, SOL, TRON)

_B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


def _b58encode(raw: bytes) -> str:
    num = int.from_bytes(raw, "big")
    out = ""
    while num:
        num, rem = divmod(num, 58)
        out = _B58[rem] + out
    return "1" * (len(raw) - len(raw.lstrip(b"\x00"))) + out


# ── the seal ────────────────────────────────────────────────────────────────

def configured() -> bool:
    """Is there a master key to seal with. Nothing is stored without one."""
    return bool(str(getattr(settings, "wallet_master_key", "") or "").strip())


def _fernet():
    """The cipher, built from the environment's master key.

    Derived rather than used raw so the operator can set any passphrase and
    still get a valid Fernet key — a setting that silently rejects most values
    is a setting that ends up unset.
    """
    from cryptography.fernet import Fernet
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.kdf.hkdf import HKDF

    secret = str(settings.wallet_master_key).encode("utf-8")
    material = HKDF(algorithm=hashes.SHA256(), length=32,
                    salt=b"sightline-wallet-v1",
                    info=b"trading key vault").derive(secret)
    return Fernet(base64.urlsafe_b64encode(material))


def _seal(raw: bytes) -> str:
    return _fernet().encrypt(raw).decode("ascii")


def _open(blob: str) -> bytes:
    return _fernet().decrypt(blob.encode("ascii"))


# ── deriving the address a key controls ─────────────────────────────────────

def _evm_address(raw: bytes) -> str:
    from eth_account import Account
    return Account.from_key(raw).address


def _sol_address(raw: bytes) -> str:
    """Solana keys are 64 bytes: a 32-byte seed then its public key.

    Wallets export both shapes, so both are accepted — but the public half of
    a 64-byte export is checked against the seed rather than trusted, because
    a mismatched pair would store a key that signs for an address we would
    then show as somebody's wallet.
    """
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    seed = raw[:32]
    priv = Ed25519PrivateKey.from_private_bytes(seed)
    pub = priv.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw)
    if len(raw) == 64 and raw[32:] != pub:
        raise ValueError("That key's two halves do not match each other")
    return _b58encode(pub)


def _tron_address(raw: bytes) -> str:
    """Tron uses the same curve as Ethereum, with its own address encoding:
    keccak of the public key, last twenty bytes, a 0x41 prefix, base58check."""
    import hashlib
    from eth_account import Account
    from eth_utils import keccak

    acct = Account.from_key(raw)
    body = bytes.fromhex(acct.address[2:])
    payload = b"\x41" + body
    checksum = hashlib.sha256(hashlib.sha256(payload).digest()).digest()[:4]
    return _b58encode(payload + checksum)


def _parse(kind: str, text: str) -> tuple[bytes, str]:
    """(raw key, the address it controls). Raises ValueError, never echoing it.

    Error messages here deliberately never quote the input. A key pasted into
    the wrong field must not come back out in a message, a toast, or a log.
    """
    text = (text or "").strip()
    if not text:
        raise ValueError("No private key given")
    try:
        if kind in (EVM, TRON):
            raw = bytes.fromhex(text[2:] if text.startswith("0x") else text)
            if len(raw) != 32:
                raise ValueError("An EVM private key is 32 bytes (64 hex characters)")
            return raw, (_evm_address(raw) if kind == EVM else _tron_address(raw))
        if kind == SOL:
            from .wallets import _b58decode
            raw = (bytes.fromhex(text[2:]) if text.startswith("0x")
                   else _b58decode(text))
            if len(raw) not in (32, 64):
                raise ValueError("A Solana private key is 32 or 64 bytes")
            return raw, _sol_address(raw)
    except ValueError:
        raise
    except Exception:  # noqa: BLE001
        # Whatever went wrong, the message must not carry the input with it.
        raise ValueError("That is not a valid private key for this chain")
    raise ValueError(f"{kind} is not a chain this holds keys for")


# ── the vault ───────────────────────────────────────────────────────────────

async def create(user: str, kind: str) -> dict:
    """A brand new wallet, made here and sealed here.

    Offered ahead of importing because it bounds the loss: this wallet holds
    what the person deliberately sends it and nothing else, so the worst case
    is the float they chose rather than everything they own.
    """
    if not configured():
        raise ValueError("The key vault is not set up — WALLET_MASTER_KEY is "
                         "not configured on this server.")
    import secrets
    raw = secrets.token_bytes(32)
    if kind == SOL:
        # Stored as the 32-byte seed; the public half is derivable and adding
        # it would only be another copy of the same secret.
        address = _sol_address(raw)
    elif kind == TRON:
        address = _tron_address(raw)
    elif kind == EVM:
        address = _evm_address(raw)
    else:
        raise ValueError(f"{kind} is not a chain this holds keys for")
    return await _store(user, kind, raw, address, "created")


async def import_key(user: str, kind: str, private_key: str) -> dict:
    """Take a key the person already has. Never echoes it back."""
    if not configured():
        raise ValueError("The key vault is not set up — WALLET_MASTER_KEY is "
                         "not configured on this server.")
    raw, address = _parse(kind, private_key)
    return await _store(user, kind, raw, address, "imported")


async def _store(user: str, kind: str, raw: bytes, address: str,
                 source: str) -> dict:
    now = time.time()
    await db.get_collection("trading_keys").update_one(
        {"user": user, "kind": kind},
        {"$set": {"user": user, "kind": kind, "address": address,
                  "blob": _seal(raw), "source": source,
                  "dt": datetime.now(timezone.utc)},
         "$setOnInsert": {"created_at": now}},
        upsert=True)
    # The address, never the key — and the log is written on the assumption
    # that somebody hostile will one day read it.
    log.info(f"[KEYS] {user} {source} a {kind} trading wallet {address[:10]}…")
    return {"kind": kind, "address": address, "source": source,
            "created_at": now}


async def address_for(user: str, kind: str) -> str:
    row = await db.get_collection("trading_keys").find_one(
        {"user": user, "kind": kind})
    return (row or {}).get("address", "")


async def listing(user: str) -> list[dict]:
    """What wallets exist, described without their secrets."""
    rows = await db.get_collection("trading_keys").find(
        {"user": user}).to_list(20)
    return [{"kind": r.get("kind"), "address": r.get("address", ""),
             "source": r.get("source", ""), "created_at": r.get("created_at")}
            for r in sorted(rows, key=lambda r: r.get("created_at") or 0)]


async def forget(user: str, kind: str) -> bool:
    """Delete the key. Irreversible, and deliberately so.

    Anything still in a created wallet becomes unreachable the moment this
    runs — there is no copy anywhere. The caller is responsible for warning
    about that before it is called.
    """
    res = await db.get_collection("trading_keys").delete_many(
        {"user": user, "kind": kind})
    gone = bool(getattr(res, "deleted_count", 0))
    if gone:
        log.info(f"[KEYS] {user} deleted their {kind} trading wallet")
    return gone


async def signer(user: str, kind: str) -> Optional[bytes]:
    """The plaintext key, for signing, for as long as one call takes.

    The only function in the codebase that produces one. It is not exposed by
    any route, and callers must not store what it returns — the point of the
    vault is that the plaintext exists in one place for one moment.
    """
    row = await db.get_collection("trading_keys").find_one(
        {"user": user, "kind": kind})
    if not row or not row.get("blob"):
        return None
    try:
        return _open(row["blob"])
    except Exception as exc:  # noqa: BLE001
        # Almost always a changed master key. Said clearly, because the
        # alternative is somebody concluding their wallet was emptied.
        log.error(f"[KEYS] cannot open {user}'s {kind} key: {type(exc).__name__} "
                  f"— has WALLET_MASTER_KEY changed?")
        return None
