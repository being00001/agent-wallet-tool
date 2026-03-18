"""
Example: USDG Auto-Claim Integration with agent_wallet.py
===========================================================

Demonstrates how to use the usdg_auto_claim module together with
agent_wallet.py for comprehensive autonomous agent wallet management.

This integration enables agents to:
1. Check wallet status and balances using agent_wallet
2. Monitor USDG rewards with usdg_auto_claim
3. Auto-sweep rewards to treasury when threshold is met
4. Track all transactions and claims in history

Usage:
    python examples/usdg_claim_integration.py --wallet <PUBKEY> --treasury <TREASURY>

Prerequisites:
    pip install solana solders
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import os
from pathlib import Path
from typing import Optional

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

# Import from agent_wallet module
try:
    from agent_wallet import (
        agent_wallet_status,
        get_transaction_history,
        WalletStatus,
        TransactionInfo,
    )
    AGENT_WALLET_AVAILABLE = True
except ImportError:
    AGENT_WALLET_AVAILABLE = False

# Import from usdg_auto_claim module
from usdg_auto_claim import (
    USDGClaimer,
    ClaimConfig,
    ClaimableBalance,
    SweepResult,
    RewardSource,
    check_claimable,
    estimate_claim_gas,
    execute_sweep,
    USDG_MINT_MAINNET,
    USDC_MINT_MAINNET,
    load_keypair,
)


# Default treasury for demo
DEFAULT_TREASURY = "3WJxpvbexvubm5p8rLVdAXEuzQ725VPxUbALvdeXZiXb"


# =============================================================================
# Integration Example Functions
# =============================================================================

def example_wallet_status_integration():
    """
    Example 1: Get comprehensive wallet status using agent_wallet.
    """
    print("=" * 70)
    print("Example 1: Wallet Status with agent_wallet.py")
    print("=" * 70)

    if not AGENT_WALLET_AVAILABLE:
        print("agent_wallet module not available. Skipping.")
        return None

    # Use default wallet from agent_wallet
    status = agent_wallet_status(network="mainnet")

    print(f"\nWallet: {status.wallet}")
    print(f"Network: {status.network}")
    print(f"SOL Balance: {status.sol_balance}")
    print(f"Is Active: {status.is_active}")

    if status.tokens:
        print("\nToken Balances:")
        for token in status.tokens:
            print(f"  {token.symbol}: {token.balance}")

    if status.error:
        print(f"\nError: {status.error}")

    return status


def example_transaction_history_integration():
    """
    Example 2: Get transaction history using agent_wallet.
    """
    print("\n" + "=" * 70)
    print("Example 2: Transaction History with agent_wallet.py")
    print("=" * 70)

    if not AGENT_WALLET_AVAILABLE:
        print("agent_wallet module not available. Skipping.")
        return []

    txs = get_transaction_history(limit=5, network="mainnet")

    print(f"\nRecent Transactions: {len(txs)}")
    for tx in txs:
        status_str = "✓" if tx.success else "✗"
        print(f"  [{status_str}] {tx.signature[:32]}...")
        print(f"      Slot: {tx.slot}, Fee: {tx.fee} lamports")

    return txs


async def example_check_usdg_balance():
    """
    Example 3: Check USDG balance using usdg_auto_claim.
    """
    print("\n" + "=" * 70)
    print("Example 3: Check USDG Balance")
    print("=" * 70)

    wallet_str = "3WJxpvbexvubm5p8rLVdAXEuzQ725VPxUbALvdeXZiXb"
    from solders.pubkey import Pubkey
    wallet = Pubkey.from_string(wallet_str)

    config = ClaimConfig(
        network="mainnet",
        threshold_lamports=1_000_000,  # 1 USDG threshold
        reward_source=RewardSource.USDG,
    )

    claim = await check_claimable(wallet, config)

    print(f"\nWallet: {claim.wallet}")
    print(f"Token: {claim.token_symbol} ({claim.token_mint})")
    print(f"Balance: {claim.balance_human} {claim.token_symbol}")
    print(f"Threshold: {claim.threshold_raw / 1_000_000} {claim.token_symbol}")
    print(f"Exceeds Threshold: {claim.exceeds_threshold}")
    print(f"SOL Balance: {claim.sol_balance_raw / 1e9} SOL")
    print(f"Can Sweep: {claim.can_sweep}")

    return claim


async def example_estimate_gas():
    """
    Example 4: Estimate gas before claiming.
    """
    print("\n" + "=" * 70)
    print("Example 4: Gas Estimation")
    print("=" * 70)

    wallet_str = "3WJxpvbexvubm5p8rLVdAXEuzQ725VPxUbALvdeXZiXb"
    treasury_str = DEFAULT_TREASURY

    from solders.pubkey import Pubkey
    wallet = Pubkey.from_string(wallet_str)
    treasury = Pubkey.from_string(treasury_str)

    config = ClaimConfig(
        network="mainnet",
        reward_source=RewardSource.USDG,
    )

    gas = await estimate_claim_gas(wallet, treasury, config)

    print(f"\nGas Estimate:")
    print(f"  Fee per CU: {gas.fee_per_cu} micro lamports")
    print(f"  Compute Units: {gas.compute_units}")
    print(f"  Estimated SOL: {gas.estimated_sol:.9f}")
    print(f"  Jito Tip: {gas.jito_tip} lamports")
    print(f"  Total: {gas.total_lamports} lamports ({gas.total_sol:.9f} SOL)")

    return gas


def example_claimer_configuration():
    """
    Example 5: Configure USDGClaimer with various options.
    """
    print("\n" + "=" * 70)
    print("Example 5: USDGClaimer Configuration")
    print("=" * 70)

    # Example 1: Basic configuration
    config_basic = ClaimConfig(
        network="mainnet",
        threshold_lamports=10_000_000,  # 10 USDG
    )

    # Example 2: With custom RPC and gas optimization
    config_optimized = ClaimConfig(
        network="mainnet",
        rpc_url="https://api.mainnet-beta.solana.com",
        threshold_lamports=5_000_000,  # 5 USDG
        simulate_before_send=True,
    )

    # Example 3: With Jito for fast confirmation
    config_fast = ClaimConfig(
        network="mainnet",
        threshold_lamports=1_000_000,
        priority_fee_config={
            "use_jito": True,
            "jito_tip": 1_000_000,
        },
    )

    print("\nConfiguration Examples:")
    print("\n1. Basic:")
    print(f"   Network: {config_basic.network}")
    print(f"   Threshold: {config_basic.threshold_lamports / 1_000_000} USDG")
    print(f"   Simulate: {config_basic.simulate_before_send}")

    print("\n2. Optimized:")
    print(f"   RPC: {config_optimized.rpc}")
    print(f"   Threshold: {config_optimized.threshold_lamports / 1_000_000} USDG")

    print("\n3. Fast (Jito):")
    print(f"   Use Jito: {config_fast.priority_fee_config.use_jito}")
    print(f"   Jito Tip: {config_fast.priority_fee_config.jito_tip} lamports")

    return [config_basic, config_optimized, config_fast]


async def example_full_claiming_workflow():
    """
    Example 6: Full claiming workflow combining agent_wallet and usdg_auto_claim.
    """
    print("\n" + "=" * 70)
    print("Example 6: Full Claiming Workflow")
    print("=" * 70)

    if not AGENT_WALLET_AVAILABLE:
        print("agent_wallet module not available. Skipping workflow demo.")
        return None

    wallet_str = "3WJxpvbexvubm5p8rLVdAXEuzQ725VPxUbALvdeXZiXb"
    treasury_str = DEFAULT_TREASURY

    from solders.pubkey import Pubkey
    wallet = Pubkey.from_string(wallet_str)
    treasury = Pubkey.from_string(treasury_str)

    # Step 1: Check overall wallet status
    print("\n[Step 1] Checking wallet status...")
    status = agent_wallet_status(wallet=wallet_str, network="mainnet")
    print(f"  SOL Balance: {status.sol_balance}")
    print(f"  Active: {status.is_active}")

    # Step 2: Check USDG balance
    print("\n[Step 2] Checking USDG balance...")
    config = ClaimConfig(network="mainnet", threshold_lamports=1_000_000)
    claim = await check_claimable(wallet, config)
    print(f"  USDG Balance: {claim.balance_human}")
    print(f"  Can Sweep: {claim.can_sweep}")

    # Step 3: Estimate gas
    print("\n[Step 3] Estimating gas...")
    gas = await estimate_claim_gas(wallet, treasury, config)
    print(f"  Estimated Cost: {gas.total_sol:.9f} SOL")

    # Step 4: Verify transaction history
    print("\n[Step 4] Checking recent transactions...")
    txs = get_transaction_history(wallet=wallet_str, limit=3, network="mainnet")
    print(f"  Recent TXs: {len(txs)}")

    # Summary
    print("\n[Summary]")
    print(f"  Wallet: {wallet_str}")
    print(f"  USDG: {claim.balance_human} (threshold: 1.0)")
    print(f"  SOL for fees: {status.sol_balance:.4f}")
    print(f"  Estimated claim cost: {gas.total_sol:.9f} SOL")

    if claim.can_sweep:
        print("\n  ✓ Ready to claim!")
    else:
        print("\n  ✗ Cannot claim yet (below threshold or insufficient SOL)")

    return {
        "status": status,
        "claim": claim,
        "gas": gas,
        "txs": txs,
    }


async def example_monitoring_loop_simulation():
    """
    Example 7: Simulated monitoring loop.
    """
    print("\n" + "=" * 70)
    print("Example 7: Monitoring Loop Simulation")
    print("=" * 70)

    wallet_str = "3WJxpvbexvubm5p8rLVdAXEuzQ725VPxUbALvdeXZiXb"
    treasury_str = DEFAULT_TREASURY

    from solders.pubkey import Pubkey
    wallet = Pubkey.from_string(wallet_str)
    treasury = Pubkey.from_string(treasury_str)

    config = ClaimConfig(
        network="mainnet",
        threshold_lamports=1_000_000,
        poll_interval_seconds=30,
    )

    print(f"\nMonitoring wallet: {wallet_str}")
    print(f"Network: {config.network}")
    print(f"Threshold: {config.threshold_lamports / 1_000_000} USDG")
    print(f"Poll Interval: {config.poll_interval_seconds}s")
    print("\nSimulating 3 monitoring cycles...")

    # Simulate monitoring
    import time
    for i in range(3):
        claim = await check_claimable(wallet, config)
        print(f"\n  Cycle {i+1}:")
        print(f"    Balance: {claim.balance_human} USDG")
        print(f"    Can Sweep: {claim.can_sweep}")

        if claim.can_sweep:
            gas = await estimate_claim_gas(wallet, treasury, config)
            print(f"    Est. Gas: {gas.total_sol:.9f} SOL")

        time.sleep(0.1)  # Short delay for demo

    print("\nMonitoring simulation complete")


def example_reward_source_options():
    """
    Example 8: Different reward source configurations.
    """
    print("\n" + "=" * 70)
    print("Example 8: Reward Source Options")
    print("=" * 70)

    sources = [
        (RewardSource.USDG, "USDG (Global Dollar by Paxos)"),
        (RewardSource.USDC, "USDC (Circle)"),
        (RewardSource.USDT, "USDT (Tether)"),
        (RewardSource.SUPERTEAM_EARN, "Superteam Earn Rewards"),
    ]

    print("\nSupported Reward Sources:")
    for source, description in sources:
        config = ClaimConfig(reward_source=source)
        print(f"\n  {source.value}:")
        print(f"    Description: {description}")
        print(f"    Mint: {config.mint_pubkey}")
        print(f"    Symbol: {config.token_symbol}")


def example_error_handling():
    """
    Example 9: Error handling patterns.
    """
    print("\n" + "=" * 70)
    print("Example 9: Error Handling")
    print("=" * 70)

    from usdg_auto_claim import (
        InsufficientFundsError,
        ClaimEligibilityError,
        RPCError,
        CircuitBreakerError,
    )

    print("\nException Types:")
    print(f"  - InsufficientFundsError: Raised when SOL/token balance is too low")
    print(f"  - ClaimEligibilityError: Raised when claim conditions aren't met")
    print(f"  - RPCError: Raised on RPC failures (with retryable flag)")
    print(f"  - CircuitBreakerError: Raised when circuit breaker is open")

    print("\nHandling Example:")
    print("""
    try:
        result = await claimer.check_and_claim(treasury)
        if result.success:
            print(f"Swept {result.amount_swept} tokens")
        else:
            print(f"Failed: {result.error}")
    except InsufficientFundsError as e:
        print(f"Insufficient funds: {e}")
    except CircuitBreakerError as e:
        print(f"Service unavailable: {e}")
        # Wait and retry later
    """)


# =============================================================================
# Main Entry Point
# =============================================================================

async def run_example(example_num: Optional[int] = None):
    """Run integration examples."""

    examples = [
        ("Wallet Status", example_wallet_status_integration),
        ("Transaction History", lambda: example_transaction_history_integration()),
        ("USDG Balance", example_check_usdg_balance),
        ("Gas Estimation", example_estimate_gas),
        ("Claimer Config", lambda: example_claimer_configuration()),
        ("Full Workflow", example_full_claiming_workflow),
        ("Monitoring", example_monitoring_loop_simulation),
        ("Reward Sources", example_reward_source_options),
        ("Error Handling", example_error_handling),
    ]

    if example_num is not None:
        if 1 <= example_num <= len(examples):
            name, func = examples[example_num - 1]
            print(f"\n>>> Running Example {example_num}: {name}")
            if asyncio.iscoroutinefunction(func):
                await func()
            else:
                func()
        else:
            print(f"Invalid example number: {example_num}")
            return 1
    else:
        # Run all examples
        for i, (name, func) in enumerate(examples, 1):
            print(f"\n>>> Running Example {i}: {name}")
            if asyncio.iscoroutinefunction(func):
                await func()
            else:
                func()

    print("\n" + "=" * 70)
    print("All examples completed!")
    print("=" * 70)

    return 0


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="USDG Auto-Claim Integration Examples"
    )
    parser.add_argument(
        "--example", "-e",
        type=int,
        help="Run specific example (1-9)"
    )
    parser.add_argument(
        "--json", "-j",
        action="store_true",
        help="Output as JSON where applicable"
    )
    parser.add_argument(
        "--wallet", "-w",
        default="3WJxpvbexvubm5p8rLVdAXEuzQ725VPxUbALvdeXZiXb",
        help="Wallet public key"
    )
    parser.add_argument(
        "--treasury", "-t",
        default=DEFAULT_TREASURY,
        help="Treasury address"
    )
    parser.add_argument(
        "--network", "-n",
        default="mainnet",
        choices=["mainnet", "devnet", "testnet"],
        help="Solana network"
    )

    args = parser.parse_args()

    return asyncio.run(run_example(args.example))


if __name__ == "__main__":
    sys.exit(main())
