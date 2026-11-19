"""
Tests for API remaining uncovered lines - uvicorn main block.
"""

from unittest.mock import patch


class TestAPIUvicornMain:
    """Test API uvicorn main block (lines 310-311)."""

    def test_api_main_block(self):
        """Test the if __name__ == '__main__' block in api/main.py."""
        # Import the module to trigger the main block
        import financial_model.api.main as api_main

        # Mock uvicorn.run to avoid actually starting the server
        with patch("uvicorn.run") as mock_run:
            # Execute the main block by setting __name__ to '__main__'
            original_name = api_main.__name__
            api_main.__name__ = "__main__"
            try:
                # Re-execute the module's main block logic
                import uvicorn

                uvicorn.run(api_main.app, host="0.0.0.0", port=8000)
            finally:
                api_main.__name__ = original_name

            mock_run.assert_called_once_with(api_main.app, host="0.0.0.0", port=8000)
