"""Linking a wallet to an account — and unlinking it.

A wallet is linked by proving control of it, not by typing it. The browser
extension signs a sentence; this checks the signature; the address is stored.
What is stored is a public address and nothing else — there is no field here
that could hold a key, and no code path that asks for one.

The sentence itself is deliberately plain English and deliberately not a
transaction. A signing prompt is the moment a person decides whether to trust
software, and the only honest thing to show them is exactly what they are
agreeing to. It costs no gas, moves nothing, and says so in its own text.

Nonces are single-use and short-lived, because a signature is a bearer token:
without one, a captured signature would link that wallet to somebody else's
account for ever. Issued, spent once, deleted.

Unlinking is a delete and nothing more. There is nothing to revoke on the
chain — we were only ever reading.
"""

from __future__ import annotations

import secrets
import time
from datetime import datetime, timezone

from . import db
from .scanners.slog import get_logger

log = get_logger(__name__)

# Long enough that somebody can read the prompt before agreeing to it, short
# enough that a signature left on a screen is worthless by the time it is seen.
NONCE_TTL = 10 * 60

EVM, SOL = "evm", "sol"
SOURCES = ("metamask", "phantom", "manual")

_B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


def _b58decode(s: str) -> bytes:
    """Base58 without a dependency.

    Solana addresses and signatures are short, the arithmetic is six lines,
    and one fewer package is one fewer thing to keep patched.
    """
    num = 0
    for ch in s:
        idx = _B58.find(ch)
        if idx < 0:
            raise ValueError("not base58")
        num = num * 58 + idx
    out = num.to_bytes((num.bit_length() + 7) // 8, "big")
    # Leading zero bytes encode as '1' and are lost by the arithmetic above.
    pad = len(s) - len(s.lstrip("1"))
    return b"\x00" * pad + out


def message_for(username: str, address: str, nonce: str) -> str:
    """What the wallet will show the person. Readable on purpose.

    Every line is one they can check against what they expected to see. A
    prompt full of hex is a prompt nobody reads, and a prompt nobody reads is
    how people sign things they did not mean to.
    """
    when = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    return (
        "SightLine — link this wallet\n"
        "\n"
        "Signing this proves the wallet is yours. It is not a transaction:\n"
        "it costs no gas, moves no funds, and grants no spending permission.\n"
        "\n"
        f"Account: {username}\n"
        f"Wallet:  {address}\n"
        f"Nonce:   {nonce}\n"
        f"Issued:  {when}\n"
    )


async def new_nonce(username: str) -> str:
    # Sweep this account's dead ones on the way past. Somebody who opens the
    # connect dialog and walks away leaves a row behind, and nothing else
    # would ever collect it — the collection has no retention rule because
    # its contents are supposed to live for ten minutes.
    await db.get_collection("wallet_nonces").delete_many(
        {"user": username, "expires_at": {"$lt": time.time()}})
    nonce = secrets.token_hex(16)
    await db.get_collection("wallet_nonces").insert_one({
        "nonce": nonce, "user": username, "expires_at": time.time() + NONCE_TTL,
        "dt": datetime.now(timezone.utc)})
    return nonce


async def _take_nonce(username: str, nonce: str) -> bool:
    """Spend it. False if it was never issued, already used, or has expired."""
    col = db.get_collection("wallet_nonces")
    row = await col.find_one({"nonce": nonce, "user": username})
    if not row:
        return False
    await col.delete_many({"nonce": nonce})
    return float(row.get("expires_at") or 0) > time.time()


# ── proving the address ─────────────────────────────────────────────────────

def _verify_evm(address: str, message: str, signature: str) -> bool:
    from eth_account import Account
    from eth_account.messages import encode_defunct
    try:
        got = Account.recover_message(encode_defunct(text=message),
                                      signature=signature)
    except Exception as exc:  # noqa: BLE001
        log.debug(f"[WALLETS] evm recover failed: {type(exc).__name__}")
        return False
    return got.lower() == address.lower()


def _verify_sol(address: str, message: str, signature: str) -> bool:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    try:
        pub = Ed25519PublicKey.from_public_bytes(_b58decode(address))
        raw = bytes.fromhex(signature[2:] if signature.startswith("0x")
                            else signature)
        pub.verify(raw, message.encode("utf-8"))
        return True
    except Exception as exc:  # noqa: BLE001
        log.debug(f"[WALLETS] sol verify failed: {type(exc).__name__}")
        return False


async def link(*, username: str, kind: str, address: str, signature: str,
               nonce: str, source: str = "manual", label: str = "") -> dict:
    """Store a wallet once its owner has proved it. Raises ValueError with why.

    Unverified wallets are not accepted at all. Reading a stranger's balance
    harms nobody, but a page headed "your portfolio" that can be filled with
    somebody else's holdings is a page that lies — and this same record is
    what a real order would later be checked against.
    """
    from . import wallet as wallet_read

    kind = (kind or "").strip().lower()
    if kind not in (EVM, SOL):
        raise ValueError("A wallet is either evm or sol")
    address = (address or "").strip()
    evm, sol = wallet_read.valid(address if kind == EVM else "",
                                 address if kind == SOL else "")
    address = evm or sol
    if not address:
        raise ValueError("No address given")

    if not await _take_nonce(username, nonce):
        raise ValueError("That signing request has expired — press connect again.")
    message = message_for(username, address, nonce)
    ok = (_verify_evm if kind == EVM else _verify_sol)(address, message,
                                                       signature or "")
    if not ok:
        raise ValueError("That signature does not match the address.")

    now = time.time()
    await db.get_collection("wallets").update_one(
        {"user": username, "address": address},
        {"$set": {"user": username, "kind": kind, "address": address,
                  "source": source if source in SOURCES else "manual",
                  "label": (label or "").strip()[:40],
                  "verified": True, "verified_at": now,
                  "dt": datetime.now(timezone.utc)},
         "$setOnInsert": {"added_at": now}},
        upsert=True)
    log.info(f"[WALLETS] {username} linked {kind} {address[:10]}… via {source}")
    return await one(username, address)


async def add_manual(*, username: str, kind: str, address: str,
                     label: str = "") -> dict:
    """A pasted address, stored as unverified.

    Kept alongside the signed path for two honest reasons. A browser
    extension needs a secure origin, so on a deployment still served over
    plain HTTP this is the only way in — and plenty of people watch a wallet
    they cannot sign for, a hardware wallet or an exchange address.

    It is never quietly promoted to verified. Reading a balance is harmless
    either way, but anything that spends must insist on the signed kind, and
    that distinction only survives if it is recorded honestly here.
    """
    from . import wallet as wallet_read

    kind = (kind or "").strip().lower()
    if kind not in (EVM, SOL):
        raise ValueError("A wallet is either evm or sol")
    evm, sol = wallet_read.valid(address if kind == EVM else "",
                                 address if kind == SOL else "")
    address = evm or sol
    if not address:
        raise ValueError("No address given")

    existing = await db.get_collection("wallets").find_one(
        {"user": username, "address": address})
    if existing and existing.get("verified"):
        # Already proved. Pasting it again must not quietly downgrade it.
        return _public(existing)

    now = time.time()
    await db.get_collection("wallets").update_one(
        {"user": username, "address": address},
        {"$set": {"user": username, "kind": kind, "address": address,
                  "source": "manual", "label": (label or "").strip()[:40],
                  "verified": False,
                  "dt": datetime.now(timezone.utc)},
         "$setOnInsert": {"added_at": now}},
        upsert=True)
    log.info(f"[WALLETS] {username} added unverified {kind} {address[:10]}…")
    return await one(username, address)


async def unlink(username: str, address: str) -> bool:
    """Forget it. Nothing to revoke — we were only ever reading."""
    res = await db.get_collection("wallets").delete_many(
        {"user": username, "address": address})
    gone = bool(getattr(res, "deleted_count", 0))
    if gone:
        log.info(f"[WALLETS] {username} unlinked {address[:10]}…")
    return gone


async def listing(username: str) -> list[dict]:
    rows = await db.get_collection("wallets").find({"user": username}).to_list(50)
    rows.sort(key=lambda r: r.get("added_at") or 0)
    return [_public(r) for r in rows]


async def one(username: str, address: str) -> dict:
    row = await db.get_collection("wallets").find_one(
        {"user": username, "address": address})
    return _public(row or {})


def _public(row: dict) -> dict:
    return {"address": row.get("address", ""), "kind": row.get("kind", ""),
            "source": row.get("source", ""), "label": row.get("label", ""),
            "verified": bool(row.get("verified")),
            "added_at": row.get("added_at")}


async def addresses(username: str) -> tuple[list[str], list[str]]:
    """(evm, sol) — what the balance reader should ask about."""
    rows = await db.get_collection("wallets").find({"user": username}).to_list(50)
    return ([r["address"] for r in rows if r.get("kind") == EVM],
            [r["address"] for r in rows if r.get("kind") == SOL])
