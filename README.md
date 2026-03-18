# Agent Wallet Tool - USDG Auto-Claim SDK for Solana

An autonomous agent SDK that detects and claims USDG (Global Dollar by Paxos) rewards on Solana, sweeping them to a treasury wallet when balances exceed a configurable threshold.

## What It Does

This SDK enables AI agents to autonomously manage Solana wallet operations:

1. **Monitor** a Solana wallet for incoming USDG token deposits
2. **Detect** when the balance exceeds a configurable threshold
3. **Sweep** tokens from the agent's operational wallet to a treasury address
4. **Track** all claim history in a local SQLite database

## How Solana Is Used

The SDK interacts directly with the Solana blockchain via RPC:

- **SPL Token Operations**: Reads token account balances using `getTokenAccountBalance`, creates Associated Token Accounts (ATAs) when needed, and executes SPL token transfers
- **Transaction Management**: Builds and signs Solana transactions with priority fee estimation, blockhash management, and confirmation polling
- **On-chain Mints**: Targets real Solana token mints — USDG (`2u1tszSeqZ3qBWF3uNGPFc8TzMk2tdiwknnRMWGWjGWH`), USDC, and USDT
- **Network Support**: Works on both devnet (with test mints) and mainnet-beta

## How the AI Agent Operates Autonomously

The `USDGClaimer` class and `monitor_and_sweep` function implement a fully autonomous claim loop:

- **Continuous Monitoring**: Polls wallet balances at configurable intervals and triggers sweeps without human intervention
- **Fault Tolerance**: Circuit breaker pattern stops operations after repeated failures, then auto-recovers after a cooldown period
- **Retry Logic**: Exponential backoff with jitter for transient RPC errors
- **Gas Optimization**: Estimates priority fees before submitting transactions to avoid overpaying or failing due to insufficient gas
- **Decision Making**: The agent decides when to claim based on balance thresholds, gas costs, and circuit breaker state

## Installation

```bash
pip install -e .
```

Or install dependencies directly:

```bash
pip install -r requirements.txt
```

### Requirements

- Python 3.10+
- A Solana keypair file (JSON format)
- Access to a Solana RPC endpoint

## Usage

### Basic: Check Claimable Balance

```python
import asyncio
from usdg_auto_claim import check_claimable, ClaimConfig

async def main():
    config = ClaimConfig(network="mainnet", threshold=10.0)
    balance = await check_claimable(
        rpc_url="https://api.mainnet-beta.solana.com",
        wallet_pubkey="YOUR_WALLET_PUBKEY",
        config=config,
    )
    print(f"Claimable: {balance.human_amount} USDG")

asyncio.run(main())
```

### Autonomous Sweep Loop

```python
import asyncio
from usdg_auto_claim import USDGClaimer, ClaimConfig

async def main():
    config = ClaimConfig(
        network="mainnet",
        threshold=10.0,
        poll_interval=60,
        reward_source="superteam_earn",
    )
    claimer = USDGClaimer(config)
    await claimer.run(
        keypair_path="./wallet.json",
        treasury="TREASURY_PUBKEY",
    )

asyncio.run(main())
```

### One-Shot Sweep

```python
import asyncio
from usdg_auto_claim import execute_sweep, ClaimConfig, load_keypair
from solders.pubkey import Pubkey

async def main():
    config = ClaimConfig(network="mainnet", threshold=5.0)
    keypair = load_keypair("./wallet.json")
    treasury = Pubkey.from_string("TREASURY_ADDRESS")

    result = await execute_sweep(
        rpc_url="https://api.mainnet-beta.solana.com",
        wallet_keypair=keypair,
        treasury=treasury,
        config=config,
    )
    print(f"Swept {result.amount_swept} lamports, tx: {result.signature}")

asyncio.run(main())
```

## Architecture

```
agent_wallet_tool/
  usdg_auto_claim.py    # Core SDK: claimer, sweep logic, history DB
  __init__.py            # Public API exports
  examples/              # Integration examples
  test_usdg_auto_claim.py  # Test suite
```

### Key Components

| Component | Purpose |
|-----------|---------|
| `USDGClaimer` | Main class — manages the autonomous claim loop |
| `ClaimConfig` | Configuration dataclass (network, threshold, fees) |
| `ClaimHistoryDB` | SQLite-backed claim history tracking |
| `CircuitBreaker` | Stops operations after repeated failures |
| `PriorityFeeEstimator` | Estimates optimal transaction fees |
| `RetryConfig` | Configurable retry with exponential backoff |

## License

MIT — see [LICENSE](LICENSE).
