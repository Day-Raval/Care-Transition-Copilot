import unittest
from unittest.mock import patch

from src.agents.tools import dispatch_tool_call


class ChatToolsTest(unittest.TestCase):
    def test_readmission_risk_tool_uses_plain_language_without_percentiles(self):
        with patch(
            "src.agents.risk_tool.assess_risk",
            return_value={
                "risk_category": "high",
                "risk_percentile": 92.0,
                "risk_score": 1.7,
                "admission_reason": "Heart failure",
            },
        ):
            result = dispatch_tool_call("assess_readmission_risk", {"patient_id": "patient-1"})

        self.assertIn("Readmission risk: high", result)
        self.assertIn("Higher concern after discharge", result)
        self.assertIn("Heart failure", result)
        self.assertNotIn("percentile", result.lower())
        self.assertNotIn("92", result)
        self.assertNotIn("1.7", result)


if __name__ == "__main__":
    unittest.main()
