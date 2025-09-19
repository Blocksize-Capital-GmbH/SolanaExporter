import unittest
from unittest.mock import patch

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
        }

    @patch("os.environ", new_callable=lambda: {})
    @patch("requests.post")
    def test_collect_metrics(self, mock_post, mock_env):
        """Test metrics collection."""
        mock_env.update(self.env)
        mock_post.return_value.status_code = 200
        mock_post.return_value.json.return_value = [
            {"result": 12345},  # getSlot
            {"result": {"value": 100_000_000_000}},  # getBalance
            {"result": {"current": [], "delinquent": []}},  # getVoteAccounts
            {"result": {"absoluteSlot": 12395, "epoch": 713}},  # getEpochInfo
            {"result": {"6jJK69aeuLbVnM6nUKnmMMwyQG2rNjKNFrfM459kfAdL": [1, 2, 3]}},  # getLeaderSchedule
            {
                "result": {"value": {"byIdentity": {"4EKxPYXmBha7ADnZphFFC13RaKNYLZCiQPKuSV8YWRZc": [1, 2]}}}
            },  # getBlockProduction
            {"result": "ok"},  # getHealth
        ]

        exporter = SolanaExporter(config_source="fromEnv")
        exporter.collect_metrics()

        self.assertEqual(exporter.slot_number._value.get(), 12345)
        self.assertEqual(exporter.balance._value.get(), 100)
        # Check that build_info contains the expected version and label strings
        build_info_labels = exporter.build_info._value
        self.assertEqual(build_info_labels.get("version"), "0.708.20306")
        self.assertEqual(build_info_labels.get("label"), "Blocksize_Testnet_Main")
        # Check that missed votes is initialized (should be 0 on first run)
        self.assertEqual(exporter.missed_votes._value.get(), 0)

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
    @patch("solanaexporter.solanaExporter.SolanaExporter._get_stake_accounts")
    def test_missed_votes_calculation(self, mock_get_stake_accounts, mock_post, mock_env):
        """Test missed votes calculation based on real-time epoch progress."""
        mock_env.update(self.env)

        # Mock stake accounts to return empty list
        mock_get_stake_accounts.return_value = []

        exporter = SolanaExporter(config_source="fromEnv")

        # Test with vote account that has current epoch credits data
        mock_post.return_value.status_code = 200
        mock_post.return_value.json.return_value = [
            {"result": 12345},  # getSlot
            {"result": {"value": 100_000_000_000}},  # getBalance
            {
                "result": {  # getVoteAccounts with epoch credits
                    "current": [
                        {
                            "votePubkey": self.env["VOTE_PUBKEY"],
                            "lastVote": 12340,
                            "activatedStake": 1000000000000,  # 1000 SOL
                            "epochCredits": [
                                [713, 130, 100],  # Current epoch 713: earned 30 credits so far (130 - 100)
                                [712, 100, 50],  # Previous epoch 712: earned 50 credits total (100 - 50)
                            ],
                        }
                    ],
                    "delinquent": [],
                }
            },
            {
                "result": {  # getEpochInfo - halfway through epoch
                    "absoluteSlot": 12345,
                    "epoch": 713,
                    "slotIndex": 21600,  # Halfway through epoch (50%)
                    "slotsInEpoch": 43200,  # Total slots in epoch
                }
            },
            {"result": {"6jJK69aeuLbVnM6nUKnmMMwyQG2rNjKNFrfM459kfAdL": [1, 2, 3]}},  # getLeaderSchedule
            {
                "result": {"value": {"byIdentity": {"4EKxPYXmBha7ADnZphFFC13RaKNYLZCiQPKuSV8YWRZc": [1, 2]}}}
            },  # getBlockProduction
            {"result": "ok"},  # getHealth
        ]

        exporter.collect_metrics()

        # Expected calculation:
        # - Current epoch progress: 50% (21600/43200)
        # - Previous epoch earned: 50 credits
        # - Expected credits by now: 50 * 0.5 = 25 credits
        # - Actual credits: 30 credits
        # - Missed votes: max(0, 25 - 30) = 0 (performing better than expected)
        self.assertEqual(exporter.missed_votes._value.get(), 0)

    @patch("os.environ", new_callable=lambda: {})
    @patch("requests.post")
    @patch("solanaexporter.solanaExporter.SolanaExporter._get_stake_accounts")
    def test_missed_votes_underperforming(self, mock_get_stake_accounts, mock_post, mock_env):
        """Test missed votes when validator is underperforming."""
        mock_env.update(self.env)

        # Mock stake accounts to return empty list
        mock_get_stake_accounts.return_value = []

        exporter = SolanaExporter(config_source="fromEnv")

        # Test case where validator is underperforming
        mock_post.return_value.status_code = 200
        mock_post.return_value.json.return_value = [
            {"result": 12345},  # getSlot
            {"result": {"value": 100_000_000_000}},  # getBalance
            {
                "result": {  # getVoteAccounts with epoch credits
                    "current": [
                        {
                            "votePubkey": self.env["VOTE_PUBKEY"],
                            "lastVote": 12340,
                            "activatedStake": 1000000000000,  # 1000 SOL
                            "epochCredits": [
                                [713, 115, 100],  # Current epoch 713: earned only 15 credits so far (115 - 100)
                                [712, 100, 50],  # Previous epoch 712: earned 50 credits total (100 - 50)
                            ],
                        }
                    ],
                    "delinquent": [],
                }
            },
            {
                "result": {  # getEpochInfo - 75% through epoch
                    "absoluteSlot": 12345,
                    "epoch": 713,
                    "slotIndex": 32400,  # 75% through epoch
                    "slotsInEpoch": 43200,  # Total slots in epoch
                }
            },
            {"result": {"6jJK69aeuLbVnM6nUKnmMMwyQG2rNjKNFrfM459kfAdL": [1, 2, 3]}},  # getLeaderSchedule
            {
                "result": {"value": {"byIdentity": {"4EKxPYXmBha7ADnZphFFC13RaKNYLZCiQPKuSV8YWRZc": [1, 2]}}}
            },  # getBlockProduction
            {"result": "ok"},  # getHealth
        ]

        exporter.collect_metrics()

        # Expected calculation:
        # - Current epoch progress: 75% (32400/43200)
        # - Previous epoch earned: 50 credits
        # - Expected credits by now: 50 * 0.75 = 37.5 credits
        # - Actual credits: 15 credits
        # - Missed votes: max(0, 37.5 - 15) = 22.5 ≈ 22 missed votes
        self.assertEqual(exporter.missed_votes._value.get(), 22)


if __name__ == "__main__":
    unittest.main()
