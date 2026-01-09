import unittest
from unittest.mock import MagicMock, patch

from solanaexporter.solanaExporter import SolanaExporter


class TestSolanaExporter(unittest.TestCase):
    def setUp(self):
        self.env = {
            "SOLANA_RPC_URL": "http://localhost:8899",
            "SOLANA_PUBLIC_RPC_URL": "https://api.testnet.solana.com",
            "EXPORTER_PORT": "7896",
            "POLL_INTERVAL": "10",
            "VOTE_PUBKEY": "6jJK69aeuLbVnM6nUKnmMMwyQG2rNjKNFrfM459kfAdL",
            "VALIDATOR_PUBKEY": "4EKxPYXmBha7ADnZphFFC13RaKNYLZCiQPKuSV8YWRZc",
            "STAKE_ACCOUNT_PUBKEY": "J1XibEzMT4pAhu6yBFs2EdsK8nSrVcCao3Ut4eYytzmw",
            "LABEL": "Blocksize_Testnet_Main",
            "VERSION": "0.708.20306",
            "DOUBLE_ZERO_FEES_ADDRESS": "11111111111111111111111111111111",
        }

    @patch("os.environ", new_callable=lambda: {})
    @patch("requests.post")
    def test_collect_metrics(self, mock_post, mock_env):
        """Test metrics collection."""
        mock_env.update(self.env)
        # collect_metrics() does getIdentity first, then a batched POST for the rest.
        resp_identity = MagicMock()
        resp_identity.status_code = 200
        resp_identity.json.return_value = [{"result": {"identity": self.env["VALIDATOR_PUBKEY"]}}]

        resp_batch = MagicMock()
        resp_batch.status_code = 200
        resp_batch.json.return_value = [
            {"result": 12345},  # getSlot
            {"result": {"value": 100_000_000_000}},  # getBalance
            {"result": {"value": 50_000_000_000}},  # getBalance (double_zero_fees_address)
            {"result": {"current": [], "delinquent": []}},  # getVoteAccounts
            {"result": {"absoluteSlot": 12395, "epoch": 713}},  # getEpochInfo
            {"result": {self.env["VALIDATOR_PUBKEY"]: [1, 2, 3]}},  # getLeaderSchedule (identity pubkey)
            {"result": {"value": {"byIdentity": {self.env["VALIDATOR_PUBKEY"]: [1, 2]}}}},  # getBlockProduction
            {"result": "ok"},  # getHealth
        ]
        mock_post.side_effect = [resp_identity, resp_batch]

        exporter = SolanaExporter(config_source="fromEnv")
        exporter.collect_metrics()

        self.assertEqual(exporter.slot_number._value.get(), 12345)
        self.assertEqual(exporter.balance._value.get(), 100)
        self.assertEqual(exporter.double_zero_balance._value.get(), 50)
        # Check that build_info contains the expected version and label strings
        build_info_labels = exporter.build_info._value
        self.assertEqual(build_info_labels.get("version"), "0.708.20306")
        self.assertEqual(build_info_labels.get("label"), "Blocksize_Testnet_Main")
        # Info gauge should exist and be set
        info_value = exporter.validator_info.labels(
            exporter.hostname,
            "unstaked",
            "unknown",
            self.env["VALIDATOR_PUBKEY"],
            self.env["VOTE_PUBKEY"],
            self.env["LABEL"],
        )._value.get()
        self.assertEqual(info_value, 1)

    @patch("requests.post")
    @patch.dict(
        "os.environ",
        {
            "SOLANA_PUBLIC_RPC_URL": "https://api.testnet.solana.com",
            "SOLANA_RPC_URL": "http://localhost:8899",
            "EXPORTER_PORT": "7896",
            "POLL_INTERVAL": "10",
            "VOTE_PUBKEY": "6jJK69aeuLbVnM6nUKnmMMwyQG2rNjKNFrfM459kfAdL",
            "VALIDATOR_PUBKEY": "4EKxPYXmBha7ADnZphFFC13RaKNYLZCiQPKuSV8YWRZc",
            "STAKE_ACCOUNT_PUBKEY": "J1XibEzMT4pAhu6yBFs2EdsK8nSrVcCao3Ut4eYytzmw",
            "VERSION": "0.708.20306",
            "LABEL": "Blocksize_Testnet_Main",
            "DOUBLE_ZERO_FEES_ADDRESS": "11111111111111111111111111111111",
        },
    )
    def test_get_stake_accounts(self, mock_post):
        """Test fetching stake accounts."""
        mock_post.return_value.status_code = 200
        mock_post.return_value.json.return_value = {
            "result": [
                {
                    "pubkey": "FpLrg2hkUnFhh9bBpFDtRJTt8VeDbqxq7SubE6kL2HX6",
                    "account": {"lamports": 1_666_666_000_000},
                }
            ]
        }

        exporter = SolanaExporter(config_source="fromEnv")
        stake_accounts = exporter._get_stake_accounts()

        self.assertEqual(len(stake_accounts), 1)
        self.assertEqual(stake_accounts[0].result[0]["account"]["lamports"], 1_666_666_000_000)

    @patch("os.environ", new_callable=lambda: {})
    @patch("requests.post")
    def test_update_stake_metrics(self, mock_post, mock_env):
        """Test updating stake metrics."""
        mock_env.update(self.env)
        mock_post.return_value.status_code = 200
        mock_post.return_value.json.return_value = [
            {
                "result": [
                    {
                        "pubkey": "FpLrg2hkUnFhh9bBpFDtRJTt8VeDbqxq7SubE6kL2HX6",
                        "account": {"lamports": 1_666_666_000_000},
                    }
                ]
            }
        ]

        exporter = SolanaExporter(config_source="fromEnv")
        vote_accounts = {
            "current": [{"votePubkey": self.env["VOTE_PUBKEY"], "activatedStake": 500_000_000_000}],
            "delinquent": [{"votePubkey": self.env["VOTE_PUBKEY"], "activatedStake": 200_000_000_000}],
        }
        exporter.stake_accounts = exporter._get_stake_accounts()
        exporter._update_stake_metrics(vote_accounts)

        self.assertEqual(exporter.total_delegated_stake._value.get(), 500)
        self.assertEqual(exporter.delinquent_stake._value.get(), 200)
        self.assertAlmostEqual(exporter.pending_stake._value.get(), 1_666.666 - 500)

    @patch("os.environ", new_callable=lambda: {})
    @patch("requests.post")
    def test_collect_metrics_without_double_zero(self, mock_post, mock_env):
        """Test metrics collection without double_zero_fees_address configured."""
        # Setup environment without DOUBLE_ZERO_FEES_ADDRESS
        env_without_double_zero = {k: v for k, v in self.env.items() if k != "DOUBLE_ZERO_FEES_ADDRESS"}
        mock_env.update(env_without_double_zero)
        resp_identity = MagicMock()
        resp_identity.status_code = 200
        resp_identity.json.return_value = [{"result": {"identity": self.env["VALIDATOR_PUBKEY"]}}]

        resp_batch = MagicMock()
        resp_batch.status_code = 200
        # Without double_zero, we have 7 requests in the batch
        resp_batch.json.return_value = [
            {"result": 12345},  # getSlot
            {"result": {"value": 100_000_000_000}},  # getBalance
            {"result": {"current": [], "delinquent": []}},  # getVoteAccounts
            {"result": {"absoluteSlot": 12395, "epoch": 713}},  # getEpochInfo
            {"result": {self.env["VALIDATOR_PUBKEY"]: [1, 2, 3]}},  # getLeaderSchedule
            {"result": {"value": {"byIdentity": {self.env["VALIDATOR_PUBKEY"]: [1, 2]}}}},  # getBlockProduction
            {"result": "ok"},  # getHealth
        ]
        mock_post.side_effect = [resp_identity, resp_batch]

        exporter = SolanaExporter(config_source="fromEnv")
        exporter.collect_metrics()

        self.assertEqual(exporter.slot_number._value.get(), 12345)
        self.assertEqual(exporter.balance._value.get(), 100)
        # double_zero_balance should not be set (or remain at initial value)

    @patch("os.environ", new_callable=lambda: {})
    @patch("requests.post")
    def test_double_zero_fees_address_specific_balance(self, mock_post, mock_env):
        """Test that double_zero_fees_address balance is correctly retrieved and converted.

        This test verifies the fix for the issue where the balance was showing as zero
        when the correct environment variable DOUBLE_ZERO_FEES_ADDRESS is used.
        Test uses address 4wm9PFxxRox3vgntwVdwbqvkRDjyjaqEdSiohosEJSj5 with expected balance ~4.89 SOL.
        """
        env_with_specific_address = self.env.copy()
        env_with_specific_address["DOUBLE_ZERO_FEES_ADDRESS"] = "4wm9PFxxRox3vgntwVdwbqvkRDjyjaqEdSiohosEJSj5"
        mock_env.update(env_with_specific_address)

        # collect_metrics() does getIdentity first, then a batched POST for the rest.
        resp_identity = MagicMock()
        resp_identity.status_code = 200
        resp_identity.json.return_value = [{"result": {"identity": self.env["VALIDATOR_PUBKEY"]}}]

        # Mock the batch response with 4.89 SOL (in lamports: 4.89 * 1_000_000_000 = 4_890_000_000)
        resp_batch = MagicMock()
        resp_batch.status_code = 200
        resp_batch.json.return_value = [
            {"result": 12345},  # getSlot
            {"result": {"value": 100_000_000_000}},  # getBalance (validator)
            {"result": {"value": 4_890_000_000}},  # getBalance (double_zero_fees_address: 4.89 SOL)
            {"result": {"current": [], "delinquent": []}},  # getVoteAccounts
            {"result": {"absoluteSlot": 12395, "epoch": 713}},  # getEpochInfo
            {"result": {self.env["VALIDATOR_PUBKEY"]: [1, 2, 3]}},  # getLeaderSchedule
            {"result": {"value": {"byIdentity": {self.env["VALIDATOR_PUBKEY"]: [1, 2]}}}},  # getBlockProduction
            {"result": "ok"},  # getHealth
        ]
        mock_post.side_effect = [resp_identity, resp_batch]

        exporter = SolanaExporter(config_source="fromEnv")
        exporter.collect_metrics()

        # Verify the balance is correctly set to 4.89 SOL (not 0)
        self.assertEqual(exporter.double_zero_balance._value.get(), 4.89)
        self.assertGreater(exporter.double_zero_balance._value.get(), 0)
        # Verify the address was correctly read from config
        self.assertEqual(exporter.config.double_zero_fees_address, "4wm9PFxxRox3vgntwVdwbqvkRDjyjaqEdSiohosEJSj5")

    @patch("os.environ", new_callable=lambda: {})
    @patch("requests.post")
    def test_identity_role_label(self, mock_post, mock_env):
        """identity_role should reflect getIdentity when STAKED/UNSTAKED keys are configured."""
        env = dict(self.env)
        env["STAKED_IDENTITY_PUBKEY"] = "IDENTITY_STAKED"
        env["UNSTAKED_IDENTITY_PUBKEY"] = "IDENTITY_UNSTAKED"
        env["VALIDATOR_PUBKEY"] = "IDENTITY_STAKED"
        mock_env.update(env)

        resp_identity = MagicMock()
        resp_identity.status_code = 200
        resp_identity.json.return_value = [{"result": {"identity": "IDENTITY_UNSTAKED"}}]

        resp_batch = MagicMock()
        resp_batch.status_code = 200
        # Minimal batch response (double_zero is configured in env, so include it)
        resp_batch.json.return_value = [
            {"result": 1},  # getSlot
            {"result": {"value": 0}},  # getBalance (active identity)
            {"result": {"value": 0}},  # getBalance (double_zero_fees_address)
            {"result": {"current": [], "delinquent": []}},  # getVoteAccounts
            {"result": {"absoluteSlot": 1, "epoch": 1}},  # getEpochInfo
            {"result": {}},  # getLeaderSchedule
            {"result": {"value": {"byIdentity": {}}}},  # getBlockProduction
            {"result": "ok"},  # getHealth
        ]
        mock_post.side_effect = [resp_identity, resp_batch]

        exporter = SolanaExporter(config_source="fromEnv")
        exporter.collect_metrics()

        info_value = exporter.validator_info.labels(
            exporter.hostname,
            "unstaked",  # from vote accounts (empty)
            "unstaked",
            "IDENTITY_UNSTAKED",
            env["VOTE_PUBKEY"],
            env["LABEL"],
        )._value.get()
        self.assertEqual(info_value, 1)

    @patch("os.environ", new_callable=lambda: {})
    @patch("requests.post")
    def test_identity_role_unknown_when_getidentity_fails(self, mock_post, mock_env):
        """identity_role should be unknown when getIdentity cannot be retrieved."""
        env = dict(self.env)
        env["STAKED_IDENTITY_PUBKEY"] = "IDENTITY_STAKED"
        env["UNSTAKED_IDENTITY_PUBKEY"] = "IDENTITY_UNSTAKED"
        # VALIDATOR_PUBKEY may be configured as the staked identity, but labels must not lie.
        env["VALIDATOR_PUBKEY"] = "IDENTITY_STAKED"
        mock_env.update(env)

        # Simulate getIdentity failure (HTTP ok but RPC error payload is treated as unsuccessful)
        resp_identity = MagicMock()
        resp_identity.status_code = 200
        resp_identity.json.return_value = [{"error": {"code": -32000, "message": "node is behind"}}]

        resp_batch = MagicMock()
        resp_batch.status_code = 200
        resp_batch.json.return_value = [
            {"result": 1},  # getSlot
            {"result": {"value": 0}},  # getBalance (falls back to VALIDATOR_PUBKEY for query)
            {"result": {"value": 0}},  # getBalance (double_zero_fees_address)
            {"result": {"current": [], "delinquent": []}},  # getVoteAccounts
            {"result": {"absoluteSlot": 1, "epoch": 1}},  # getEpochInfo
            {"result": {}},  # getLeaderSchedule
            {"result": {"value": {"byIdentity": {}}}},  # getBlockProduction
            {"result": "ok"},  # getHealth
        ]
        mock_post.side_effect = [resp_identity, resp_batch]

        exporter = SolanaExporter(config_source="fromEnv")
        exporter.collect_metrics()

        info_value = exporter.validator_info.labels(
            exporter.hostname,
            "unstaked",  # from empty vote accounts => 0 stake => unstaked
            "unknown",
            "unknown",
            env["VOTE_PUBKEY"],
            env["LABEL"],
        )._value.get()
        self.assertEqual(info_value, 1)


if __name__ == "__main__":
    unittest.main()
