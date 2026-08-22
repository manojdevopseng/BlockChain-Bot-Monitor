"""Can this wallet actually sell this token — asked by trying it.

Every other check here reads what a pool looks like from outside: how much
liquidity, how many people bought, how many got out. That catches an empty
pool and a token nobody has escaped from, and it is blind to the one trick it
most needs to catch.

A whitelist honeypot has a healthy pool. Liquidity is real, buys are real,
and the sells are real too — they are the developer's own addresses, and the
contract refuses everybody else. From outside it looks like a token doing
well. The only way to tell is to try to sell and see what happens.

So that is what this does. It gives the buying wallet some of the token — not
by buying it, but by rewriting that one storage slot for the duration of a
single `eth_call` — and then asks the chain to move it to the pool. Nothing
is signed, nothing is sent, and the chain's own state is untouched: an
`eth_call` with a state override is a question, not a transaction.

If the transfer reverts, that wallet cannot sell. That is the answer, and it
catches the whole family at once: whitelists, blacklists, paused transfers,
and the "only the deployer may sell" pattern.

Finding the slot is the fiddly part. Solidity lays a `mapping(address =>
uint256)` at `keccak(pad(holder) . pad(slot))` and Vyper reverses the two, so
both are tried across the low slots, and the one that makes `balanceOf`
return the planted number is the real one. That costs a handful of calls
once per token and is then remembered.

What it does not catch: a token that lets the transfer through and takes all
of it in tax. That shows up as a fill far below the floor, which the
slippage check already refuses.
"""

from __future__ import annotations

import asyncio
import time
from typing import Optional

from .scanners.slog import get_logger

log = get_logger(__name__)

# Slots to probe. Balance mappings live near the top of storage in almost
# every token; twenty is generous and the search stops at the first hit.
MAX_SLOT = 20

# A verdict is kept this long. A contract can change behaviour — some turn
# selling off after launch — so this is deliberately short.
TTL = 10 * 60

# {(chain, token): (slot, is_vyper)} and {(chain, token): (ok, why, at)}
_SLOTS: dict[tuple, tuple[int, bool]] = {}
_VERDICTS: dict[tuple, tuple[bool, str, float]] = {}

_BALANCE_OF = "0x70a08231"          # balanceOf(address)
_TRANSFER = "0xa9059cbb"            # transfer(address,uint256)


def _w3(chain: str):
    from web3 import Web3
    from .scanners import scfg
    url = str(getattr(scfg, f"{chain.upper()}_RPC_HTTP", "") or "")
    if not url:
        raise ValueError(f"no RPC for {chain}")
    return Web3(Web3.HTTPProvider(url, request_kwargs={"timeout": 15}))


def _pad(value: str) -> str:
    return value.lower().replace("0x", "").rjust(64, "0")


def _slot_key(holder: str, slot: int, vyper: bool) -> str:
    """Where a balance mapping keeps this holder's entry."""
    from eth_utils import keccak
    a = bytes.fromhex(_pad(holder))
    b = bytes.fromhex(_pad(hex(slot)))
    raw = keccak(b + a) if vyper else keccak(a + b)
    return "0x" + raw.hex()


async def _find_slot(chain: str, token: str, holder: str) -> Optional[tuple[int, bool]]:
    """Which storage slot holds balances. Proved, not guessed.

    A candidate is accepted only when planting a number in it makes the
    token's own `balanceOf` return that number — so a wrong guess cannot pass.
    """
    cached = _SLOTS.get((chain, token.lower()))
    if cached:
        return cached

    from web3 import Web3
    w3 = _w3(chain)
    probe = 10 ** 24
    planted = "0x" + hex(probe)[2:].rjust(64, "0")
    call = {"to": Web3.to_checksum_address(token),
            "data": _BALANCE_OF + _pad(holder)}

    def ask(slot: int, vyper: bool):
        override = {Web3.to_checksum_address(token):
                    {"stateDiff": {_slot_key(holder, slot, vyper): planted}}}
        return w3.provider.make_request("eth_call", [call, "latest", override])

    for slot in range(MAX_SLOT):
        for vyper in (False, True):
            try:
                res = await asyncio.to_thread(ask, slot, vyper)
            except Exception:  # noqa: BLE001
                continue
            got = (res or {}).get("result") or ""
            if got and int(got, 16) == probe:
                _SLOTS[(chain, token.lower())] = (slot, vyper)
                log.debug(f"[HONEYPOT] {token[:10]} balances at slot {slot}"
                          + (" (vyper)" if vyper else ""))
                return slot, vyper
    return None


async def can_sell(chain: str, token: str, holder: str,
                   pool: str) -> tuple[bool, str]:
    """(can this wallet move the token to the pool, why not).

    Unknowable is not the same as bad. When the slot cannot be found, or the
    node will not take a state override, this returns True with a reason —
    refusing every token whose storage layout is unusual would throw away far
    more good tokens than it would catch bad ones, and the caller has other
    checks.
    """
    key = (chain, token.lower())
    hit = _VERDICTS.get(key)
    if hit and time.time() - hit[2] < TTL:
        return hit[0], hit[1]

    try:
        found = await _find_slot(chain, token, holder)
    except Exception as exc:  # noqa: BLE001
        return True, f"could not probe the token ({type(exc).__name__})"
    if not found:
        return True, "storage layout not recognised — no verdict"

    slot, vyper = found
    from web3 import Web3
    w3 = _w3(chain)
    amount = 10 ** 18
    planted = "0x" + hex(amount * 1000)[2:].rjust(64, "0")

    # Send it where a sell would send it. A token that allows transfers
    # generally and blocks them to the pool is still one nobody can sell.
    data = (_TRANSFER + _pad(pool)
            + hex(amount)[2:].rjust(64, "0"))
    call = {"from": Web3.to_checksum_address(holder),
            "to": Web3.to_checksum_address(token),
            "data": data}
    override = {
        Web3.to_checksum_address(token):
            {"stateDiff": {_slot_key(holder, slot, vyper): planted}},
        Web3.to_checksum_address(holder): {"balance": hex(10 ** 18)},
    }

    def ask():
        return w3.provider.make_request("eth_call", [call, "latest", override])

    try:
        res = await asyncio.to_thread(ask)
    except Exception as exc:  # noqa: BLE001
        return True, f"simulation could not run ({type(exc).__name__})"

    if isinstance(res, dict) and res.get("error"):
        why = str((res["error"] or {}).get("message") or "reverted")
        out = (False, f"this wallet cannot move the token: {why[:120]}")
    else:
        # A transfer that returns false rather than reverting is the quieter
        # version of the same refusal, and some tokens do exactly that.
        got = (res or {}).get("result") or ""
        if got and len(got) >= 66 and int(got, 16) == 0:
            out = (False, "the token refused the transfer without reverting")
        else:
            out = (True, "")
    _VERDICTS[key] = (out[0], out[1], time.time())
    return out
