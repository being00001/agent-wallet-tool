# Agent Wallet Tool for Solana

A unified SDK for autonomous AI agents operating on Solana.

This repo is the canonical Agent Wallet family repository under the current workspace governance rules.

It combines:
- `agent_wallet.py` — wallet balances, token discovery, transaction history, Jupiter swap stubs, RPC failover
- `dao_integration.py` — DAO discovery, proposal creation, voting, status tracking
- `usdg_auto_claim.py` — USDG/USDC/USDT auto-sweep with retry logic, circuit breaker, fee estimation, and history tracking
- `tools/realms_voter_proxy.py` — repo-local governance helper for Realms / Squads DAO workflows

## Modules

| Module | File | Description |
|---|---|---|
| Wallet Management | `agent_wallet.py` | SOL/SPL balances, tx history, Jupiter quote/swap stubs |
| DAO Integration | `dao_integration.py` | DAO discovery, proposals, voting, status tracking |
| Auto-Claim | `usdg_auto_claim.py` | Token monitoring and treasury sweep logic |
| Governance Helper | `tools/realms_voter_proxy.py` | Realms / Squads proposal and voting helper |

## Install

```bash
pip install -e .
```

Or:

```bash
pip install -r requirements.txt
```

## Requirements

- Python 3.10+
- Solana RPC access
- For sweep operations: a Solana keypair JSON file

## Quick Start

### Wallet status

```python
from agent_wallet import agent_wallet_status

status = agent_wallet_status(network="mainnet")
print(status.summary())
```

### DAO actions

```python
from dao_integration import list_daos, create_proposal

daos = list_daos("mainnet")
proposal = create_proposal(
    dao=daos[0],
    title="Agent Collective Decision",
    description="Example proposal",
    proposer_wallet="AgentWallet...",
)
print(proposal.title)
```

### Auto-claim / sweep

```python
import asyncio
from solders.pubkey import Pubkey
from usdg_auto_claim import check_claimable, ClaimConfig

async def main():
    config = ClaimConfig(network="mainnet", threshold=10.0)
    wallet = Pubkey.from_string("YOUR_WALLET")
    claim = await check_claimable(wallet, config)
    print(claim.balance_human)

asyncio.run(main())
```

## Examples

- `examples/basic_usage.py`
- `examples/integrated_agent.py`
- `examples/usdg_claim_integration.py`

## Governance Helper

The repository also carries a repo-local helper under `tools/` for DAO governance flows:

```python
from tools.realms_voter_proxy import vote_on_proposal, submit_proposal
```

This helper is intended for source-checkout use inside the repo rather than as a separately packaged module.

## Tests

```bash
pytest -q
```

## Safety Notes

- Token sweep and SOL sweep are treated separately.
- **SOL fallback is disabled by default** in `usdg_auto_claim.py` so token transfer failures do not silently turn into SOL transfers.
- To enable SOL fallback, it must be explicitly configured via `ClaimConfig(allow_sol_fallback=True)`.

## Notes

- Canonical remote: <https://github.com/being00001/agent-wallet-tool>
- Legacy family clones were consolidated into this repo during the 2026-03-19 workspace cleanup.
- This repo intentionally keeps the original flat-module layout for backward compatibility.

## License

MIT
