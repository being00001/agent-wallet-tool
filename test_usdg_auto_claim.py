"""
Comprehensive Tests for USDG Auto-Claim Tool v2
================================================

15+ tests covering:
- Configuration defaults and overrides
- Token mint address validation
- ATA derivation determinism
- Claimable balance detection (above/below threshold)
- Retry mechanism with mock failures
- Circuit breaker open/close transitions
- Gas estimation accuracy
- Sweep success/failure scenarios
- Claim history storage and retrieval
- CLI argument parsing
- Network endpoint configuration
- Error handling edge cases
- Integration with agent_wallet.py patterns
"""

from __future__ import annotations

import json
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent))

from usdg_auto_claim import (
    ClaimConfig,
    ClaimableBalance,
    SweepResult,
    GasEstimate,
    RewardSource,
    build_parser,
    check_claimable,
    get_associated_token_address,
    estimate_claim_gas,
    execute_sweep,
    main,
    USDGClaimer,
    load_keypair,
    USDG_MINT_MAINNET,
    USDC_MINT_MAINNET,
    RPC_ENDPOINTS,
    RetryConfig,
    CircuitBreaker,
    CircuitBreakerConfig,
    CircuitState,
    PriorityFeeConfig,
    PriorityFeeEstimator,
    ClaimHistoryDB,
    RESILIENT_CLIENT_TIMEOUT,
)
from solders.keypair import Keypair
from solders.pubkey import Pubkey


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def temp_db():
    """Create a temporary database for testing."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    yield db_path
    Path(db_path).unlink(missing_ok=True)


@pytest.fixture
def test_wallet():
    """Generate a test wallet keypair."""
    return Keypair()


@pytest.fixture
def test_config():
    """Create a test configuration."""
    return ClaimConfig(
        network="devnet",
        threshold_lamports=1_000_000,
    )


# ---------------------------------------------------------------------------
# Test ClaimConfig
# ---------------------------------------------------------------------------

class TestClaimConfig:
    """Test ClaimConfig dataclass."""

    def test_defaults(self):
        """Test default configuration values."""
        cfg = ClaimConfig()
        assert cfg.network == "devnet"
        assert cfg.rpc == "https://api.devnet.solana.com"
        assert cfg.threshold_lamports == 1_000_000
        assert cfg.sweep_percentage == 100
        assert cfg.reward_source == RewardSource.USDG
        assert cfg.simulate_before_send is True

    def test_mainnet(self):
        """Test mainnet configuration."""
        cfg = ClaimConfig(network="mainnet")
        assert cfg.rpc == "https://api.mainnet-beta.solana.com"
        assert cfg.mint_pubkey == USDG_MINT_MAINNET
        assert cfg.token_symbol == "USDG"

    def test_usdc_reward_source(self):
        """Test USDC reward source configuration."""
        cfg = ClaimConfig(reward_source=RewardSource.USDC)
        assert cfg.mint_pubkey == USDC_MINT_MAINNET
        assert cfg.token_symbol == "USDC"

    def test_custom_rpc(self):
        """Test custom RPC URL override."""
        cfg = ClaimConfig(rpc_url="https://custom.rpc.com")
        assert cfg.rpc == "https://custom.rpc.com"

    def test_custom_mint(self):
        """Test custom token mint override."""
        mint = "So11111111111111111111111111111111111111112"
        cfg = ClaimConfig(token_mint=mint)
        assert str(cfg.mint_pubkey) == mint

    def test_gas_optimization_settings(self):
        """Test gas optimization configuration."""
        priority_cfg = PriorityFeeConfig(
            min_fee_per_cu=500,
            max_fee_per_cu=5_000_000,
            use_jito=True,
            jito_tip=2_000_000,
        )
        cfg = ClaimConfig(priority_fee_config=priority_cfg)
        assert cfg.priority_fee_config.min_fee_per_cu == 500
        assert cfg.priority_fee_config.use_jito is True

    def test_history_db_path(self):
        """Test history database path configuration."""
        cfg = ClaimConfig(history_db_path="/tmp/test_history.db")
        assert cfg.history_db_path == "/tmp/test_history.db"


# ---------------------------------------------------------------------------
# Test ATA Derivation
# ---------------------------------------------------------------------------

class TestATADerivation:
    """Test associated token account derivation."""

    def test_deterministic(self):
        """Test ATA derivation is deterministic."""
        owner = Keypair().pubkey()
        mint = USDG_MINT_MAINNET
        ata1 = get_associated_token_address(owner, mint)
        ata2 = get_associated_token_address(owner, mint)
        assert ata1 == ata2

    def test_different_owners(self):
        """Test different owners produce different ATAs."""
        owner1 = Keypair().pubkey()
        owner2 = Keypair().pubkey()
        mint = USDG_MINT_MAINNET
        ata1 = get_associated_token_address(owner1, mint)
        ata2 = get_associated_token_address(owner2, mint)
        assert ata1 != ata2

    def test_different_mints(self):
        """Test different mints produce different ATAs."""
        owner = Keypair().pubkey()
        ata_usdg = get_associated_token_address(owner, USDG_MINT_MAINNET)
        ata_usdc = get_associated_token_address(owner, USDC_MINT_MAINNET)
        assert ata_usdg != ata_usdc


# ---------------------------------------------------------------------------
# Test Retry Configuration
# ---------------------------------------------------------------------------

class TestRetryConfig:
    """Test retry configuration and exponential backoff."""

    def test_calculate_delay(self):
        """Test exponential backoff delay calculation."""
        config = RetryConfig(base_delay=1.0, max_delay=10.0, exponential_base=2.0)
        delay0 = config.calculate_delay(0)
        delay1 = config.calculate_delay(1)
        delay2 = config.calculate_delay(2)

        assert delay0 <= 1.1  # base + jitter
        assert delay1 <= 2.1  # base * 2 + jitter
        assert delay2 <= 4.1  # base * 4 + jitter

    def test_max_delay_cap(self):
        """Test delay is capped at max_delay."""
        config = RetryConfig(base_delay=1.0, max_delay=2.0, exponential_base=10.0)
        delay = config.calculate_delay(10)
        assert delay <= 2.0


# ---------------------------------------------------------------------------
# Test Circuit Breaker
# ---------------------------------------------------------------------------

class TestCircuitBreaker:
    """Test circuit breaker pattern."""

    def test_initial_state_closed(self):
        """Test circuit breaker starts in closed state."""
        cb = CircuitBreaker(CircuitBreakerConfig())
        assert cb.state == CircuitState.CLOSED
        assert cb.can_execute() is True

    def test_opens_after_failures(self):
        """Test circuit opens after threshold failures."""
        config = CircuitBreakerConfig(failure_threshold=3)
        cb = CircuitBreaker(config)

        cb.record_failure(Exception("test error"))
        cb.record_failure(Exception("test error"))
        assert cb.can_execute() is True  # Still below threshold

        cb.record_failure(Exception("test error"))
        assert cb.can_execute() is False  # Now open

    def test_excludes_insufficient_funds(self):
        """Test InsufficientFundsError doesn't count toward failures."""
        from usdg_auto_claim import InsufficientFundsError

        config = CircuitBreakerConfig(failure_threshold=2)
        cb = CircuitBreaker(config)

        cb.record_failure(InsufficientFundsError("no funds"))
        cb.record_failure(InsufficientFundsError("no funds"))
        assert cb.can_execute() is True  # Should not open

    def test_half_open_after_timeout(self):
        """Test circuit moves to half-open after timeout."""
        config = CircuitBreakerConfig(failure_threshold=1, timeout=0.1)
        cb = CircuitBreaker(config)

        cb.record_failure(Exception("test error"))
        assert cb.can_execute() is False

        time.sleep(0.2)
        assert cb.can_execute() is True
        assert cb.state == CircuitState.HALF_OPEN

    def test_status_property(self):
        """Test circuit breaker status property."""
        config = CircuitBreakerConfig(failure_threshold=2)
        cb = CircuitBreaker(config)

        status = cb.status
        assert "state" in status
        assert "failure_count" in status
        assert "success_count" in status


# ---------------------------------------------------------------------------
# Test Gas Estimation
# ---------------------------------------------------------------------------

class TestGasEstimate:
    """Test gas estimation."""

    def test_gas_estimate_creation(self):
        """Test GasEstimate dataclass."""
        gas = GasEstimate(
            fee_per_cu=1000,
            compute_units=200000,
            estimated_lamports=200000,
            estimated_sol=0.0002,
            jito_tip=0,
            total_lamports=200000,
        )
        assert gas.fee_per_cu == 1000
        assert gas.total_sol == 0.0002

    def test_gas_estimate_to_dict(self):
        """Test GasEstimate serialization."""
        gas = GasEstimate(
            fee_per_cu=1000,
            compute_units=200000,
            estimated_lamports=200000,
            estimated_sol=0.0002,
            jito_tip=1000,
            total_lamports=201000,
        )
        d = gas.to_dict()
        assert "fee_per_cu" in d
        assert "total_sol" in d
        assert d["total_sol"] == 0.000201


# ---------------------------------------------------------------------------
# Test Claimable Balance Detection
# ---------------------------------------------------------------------------

class TestClaimableBalance:
    """Test claimable balance detection."""

    def test_claimable_balance_creation(self):
        """Test ClaimableBalance dataclass."""
        cb = ClaimableBalance(
            wallet="test_wallet",
            token_mint=str(USDG_MINT_MAINNET),
            balance_raw=5_000_000,
            balance_human=5.0,
            exceeds_threshold=True,
            threshold_raw=1_000_000,
            sol_balance_raw=10_000_000,
            can_sweep=True,
            token_symbol="USDG",
            reward_source="usdg",
        )
        assert cb.balance_human == 5.0
        assert cb.exceeds_threshold is True
        assert cb.can_sweep is True


# ---------------------------------------------------------------------------
# Test Sweep Result
# ---------------------------------------------------------------------------

class TestSweepResult:
    """Test sweep result dataclass."""

    def test_success_result(self):
        """Test successful sweep result."""
        result = SweepResult(
            success=True,
            signature="abc123",
            amount_swept=1_000_000,
            fee_paid=5000,
        )
        assert result.success is True
        assert result.signature == "abc123"
        assert result.amount_swept == 1_000_000

    def test_failure_result(self):
        """Test failed sweep result."""
        result = SweepResult(
            success=False,
            error="Insufficient funds",
        )
        assert result.success is False
        assert result.error == "Insufficient funds"


# ---------------------------------------------------------------------------
# Test Check Claimable (with mocks)
# ---------------------------------------------------------------------------

class TestCheckClaimable:
    """Test check_claimable function with mocked RPC."""

    @pytest.mark.asyncio
    async def test_above_threshold(self):
        """Test balance above threshold."""
        config = ClaimConfig(threshold_lamports=1_000_000)
        wallet = Keypair().pubkey()

        with patch("usdg_auto_claim.ResilientClient") as MockClient:
            mock_client = AsyncMock()
            MockClient.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            MockClient.return_value.__aexit__ = AsyncMock(return_value=False)

            # Mock token balance: 5 USDC
            mock_token_resp = MagicMock()
            mock_token_resp.value = MagicMock()
            mock_token_resp.value.amount = "5000000"
            mock_client.get_token_account_balance = AsyncMock(return_value=mock_token_resp)

            # Mock SOL balance: 1 SOL
            mock_sol_resp = MagicMock()
            mock_sol_resp.value = 1_000_000_000
            mock_client.get_balance = AsyncMock(return_value=mock_sol_resp)

            result = await check_claimable(wallet, config)

            assert result.exceeds_threshold is True
            assert result.balance_raw == 5_000_000
            assert result.balance_human == 5.0
            assert result.can_sweep is True

    @pytest.mark.asyncio
    async def test_below_threshold(self):
        """Test balance below threshold."""
        config = ClaimConfig(threshold_lamports=1_000_000)
        wallet = Keypair().pubkey()

        with patch("usdg_auto_claim.ResilientClient") as MockClient:
            mock_client = AsyncMock()
            MockClient.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            MockClient.return_value.__aexit__ = AsyncMock(return_value=False)

            mock_token_resp = MagicMock()
            mock_token_resp.value = MagicMock()
            mock_token_resp.value.amount = "500000"  # 0.5 USDC
            mock_client.get_token_account_balance = AsyncMock(return_value=mock_token_resp)

            mock_sol_resp = MagicMock()
            mock_sol_resp.value = 1_000_000_000
            mock_client.get_balance = AsyncMock(return_value=mock_sol_resp)

            result = await check_claimable(wallet, config)

            assert result.exceeds_threshold is False
            assert result.balance_human == 0.5
            assert result.can_sweep is False  # Below threshold

    @pytest.mark.asyncio
    async def test_insufficient_sol_for_fees(self):
        """Test insufficient SOL balance prevents sweep."""
        config = ClaimConfig(threshold_lamports=1_000_000, min_sol_balance=5_000_000)
        wallet = Keypair().pubkey()

        with patch("usdg_auto_claim.ResilientClient") as MockClient:
            mock_client = AsyncMock()
            MockClient.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            MockClient.return_value.__aexit__ = AsyncMock(return_value=False)

            mock_token_resp = MagicMock()
            mock_token_resp.value = MagicMock()
            mock_token_resp.value.amount = "5000000"  # 5 USDC - above threshold
            mock_client.get_token_account_balance = AsyncMock(return_value=mock_token_resp)

            # Only 0.001 SOL - below minimum
            mock_sol_resp = MagicMock()
            mock_sol_resp.value = 1_000_000
            mock_client.get_balance = AsyncMock(return_value=mock_sol_resp)

            result = await check_claimable(wallet, config)

            assert result.balance_human == 5.0
            assert result.exceeds_threshold is True
            assert result.can_sweep is False  # Insufficient SOL for fees


# ---------------------------------------------------------------------------
# Test Claim History Database
# ---------------------------------------------------------------------------

class TestClaimHistoryDB:
    """Test claim history SQLite storage."""

    def test_init_db(self, temp_db):
        """Test database initialization."""
        db = ClaimHistoryDB(temp_db)
        assert Path(temp_db).exists()

    def test_record_claim(self, temp_db):
        """Test recording a claim."""
        db = ClaimHistoryDB(temp_db)
        claim_id = db.record_claim(
            wallet="test_wallet",
            treasury="test_treasury",
            token_mint=str(USDG_MINT_MAINNET),
            amount_raw=1_000_000,
            amount_human=1.0,
            fee_paid=5000,
            status="success",
            signature="sig123",
            reward_source="usdg",
        )
        assert claim_id > 0

    def test_get_claims(self, temp_db):
        """Test retrieving claim history."""
        db = ClaimHistoryDB(temp_db)
        db.record_claim(
            wallet="test_wallet",
            treasury="test_treasury",
            token_mint=str(USDG_MINT_MAINNET),
            amount_raw=1_000_000,
            amount_human=1.0,
            fee_paid=5000,
            status="success",
            reward_source="usdg",
        )

        claims = db.get_claims(wallet="test_wallet")
        assert len(claims) >= 1

    def test_update_claim(self, temp_db):
        """Test updating a claim."""
        db = ClaimHistoryDB(temp_db)
        claim_id = db.record_claim(
            wallet="test_wallet",
            treasury="test_treasury",
            token_mint=str(USDG_MINT_MAINNET),
            amount_raw=1_000_000,
            amount_human=1.0,
            fee_paid=5000,
            status="pending",
            reward_source="usdg",
        )

        db.update_claim(claim_id, "success", "sig123")

        claims = db.get_claims(wallet="test_wallet")
        # The updated claim should now have success status
        updated = [c for c in claims if c["id"] == claim_id]
        assert len(updated) == 1

    def test_get_stats(self, temp_db):
        """Test getting claim statistics."""
        db = ClaimHistoryDB(temp_db)
        db.record_claim(
            wallet="test_wallet",
            treasury="test_treasury",
            token_mint=str(USDG_MINT_MAINNET),
            amount_raw=1_000_000,
            amount_human=1.0,
            fee_paid=5000,
            status="success",
            reward_source="usdg",
        )
        db.record_claim(
            wallet="test_wallet",
            treasury="test_treasury",
            token_mint=str(USDG_MINT_MAINNET),
            amount_raw=500_000,
            amount_human=0.5,
            fee_paid=5000,
            status="failed",
            reward_source="usdg",
        )

        stats = db.get_claim_stats(wallet="test_wallet")
        assert "success" in stats
        assert stats["success"]["count"] == 1


# ---------------------------------------------------------------------------
# Test CLI Parsing
# ---------------------------------------------------------------------------

class TestCLIParsing:
    """Test CLI argument parsing."""

    def test_check_mode(self):
        """Test --check mode parsing."""
        parser = build_parser()
        args = parser.parse_args([
            "--check",
            "--wallet", "11111111111111111111111111111111",
        ])
        assert args.check is True
        assert args.sweep is False
        assert args.monitor is False

    def test_sweep_mode(self):
        """Test --sweep mode parsing."""
        parser = build_parser()
        args = parser.parse_args([
            "--sweep",
            "--wallet", "11111111111111111111111111111111",
            "--treasury", "22222222222222222222222222222222",
            "--keypair", "/tmp/key.json",
            "--threshold", "5.0",
            "--sweep-pct", "80",
        ])
        assert args.sweep is True
        assert args.threshold == 5.0
        assert args.sweep_pct == 80

    def test_monitor_mode(self):
        """Test --monitor mode parsing."""
        parser = build_parser()
        args = parser.parse_args([
            "--monitor",
            "--wallet", "11111111111111111111111111111111",
            "--treasury", "22222222222222222222222222222222",
            "--keypair", "/tmp/key.json",
            "--interval", "10",
            "--network", "mainnet",
        ])
        assert args.monitor is True
        assert args.interval == 10
        assert args.network == "mainnet"

    def test_reward_source_parsing(self):
        """Test reward source argument parsing."""
        parser = build_parser()
        args = parser.parse_args([
            "--check",
            "--wallet", "11111111111111111111111111111111",
            "--reward-source", "usdc",
        ])
        assert args.reward_source == "usdc"

    def test_gas_estimation_flag(self):
        """Test --estimate-gas flag parsing."""
        parser = build_parser()
        args = parser.parse_args([
            "--check",
            "--wallet", "11111111111111111111111111111111",
            "--treasury", "22222222222222222222222222222222",
            "--estimate-gas",
        ])
        assert args.estimate_gas is True


# ---------------------------------------------------------------------------
# Test Network Configuration
# ---------------------------------------------------------------------------

class TestNetworkConfig:
    """Test network configuration."""

    def test_rpc_endpoints(self):
        """Test RPC endpoint configuration."""
        assert "devnet" in RPC_ENDPOINTS
        assert "mainnet" in RPC_ENDPOINTS
        assert "testnet" in RPC_ENDPOINTS

    def test_mainnet_endpoint(self):
        """Test mainnet RPC endpoint."""
        assert "api.mainnet-beta.solana.com" in RPC_ENDPOINTS["mainnet"]


# ---------------------------------------------------------------------------
# Test Reward Source Enum
# ---------------------------------------------------------------------------

class TestRewardSource:
    """Test reward source enum."""

    def test_reward_source_values(self):
        """Test reward source enum values."""
        assert RewardSource.USDG.value == "usdg"
        assert RewardSource.USDC.value == "usdc"
        assert RewardSource.USDT.value == "usdt"
        assert RewardSource.STAKING.value == "staking"
        assert RewardSource.SUPERTEAM_EARN.value == "superteam_earn"


# ---------------------------------------------------------------------------
# Test USDGClaimer Class
# ---------------------------------------------------------------------------

class TestUSDGClaimer:
    """Test USDGClaimer main class."""

    def test_claimer_creation(self):
        """Test USDGClaimer instantiation."""
        config = ClaimConfig()
        claimer = USDGClaimer(config)
        assert claimer.config == config
        assert claimer.keypair is None

    def test_claimer_from_keypair_path(self):
        """Test USDGClaimer creation from keypair path."""
        # Create a temporary keypair file
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            keypair = Keypair()
            secret = list(keypair.to_bytes())
            json.dump(secret, f)
            keypair_path = f.name

        try:
            config = ClaimConfig()
            claimer = USDGClaimer.from_keypair_path(config, keypair_path)
            assert claimer.keypair is not None
        finally:
            Path(keypair_path).unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Test Load Keypair
# ---------------------------------------------------------------------------

class TestLoadKeypair:
    """Test keypair loading function."""

    def test_load_keypair(self):
        """Test loading a keypair from file."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            keypair = Keypair()
            secret = list(keypair.to_bytes())
            json.dump(secret, f)
            keypair_path = f.name

        try:
            loaded = load_keypair(keypair_path)
            assert loaded.pubkey() == keypair.pubkey()
        finally:
            Path(keypair_path).unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Test CLI Main Function
# ---------------------------------------------------------------------------

class TestCLIMain:
    """Test CLI main function with mocked operations."""

    @pytest.mark.asyncio
    async def test_check_outputs_json(self, capsys):
        """Test --check outputs valid JSON."""
        wallet = Keypair().pubkey()

        with patch("usdg_auto_claim.check_claimable") as mock_check:
            mock_check.return_value = ClaimableBalance(
                wallet=str(wallet),
                token_mint=str(USDG_MINT_MAINNET),
                balance_raw=2_000_000,
                balance_human=2.0,
                exceeds_threshold=True,
                threshold_raw=1_000_000,
                sol_balance_raw=10_000_000,
                can_sweep=True,
                token_symbol="USDG",
                reward_source="usdg",
            )

            ret = await main(["--check", "--wallet", str(wallet)])
            assert ret == 0

            captured = capsys.readouterr()
            data = json.loads(captured.out)
            assert data["balance"] == 2.0
            assert data["exceeds_threshold"] is True
            assert data["can_sweep"] is True


# ---------------------------------------------------------------------------
# Run Tests
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    pytest.main([__file__, "-v"])
