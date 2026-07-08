import inngest
import os
import logging

logger = logging.getLogger(__name__)

# Initialize Inngest client.
# Set INNGEST_EVENT_KEY in .env for production.
# Use INNGEST_DEV=1 for local development.

is_dev = os.getenv("INNGEST_DEV", "0") == "1"

inngest_client = inngest.Inngest(
    app_id="insync-backend",
    is_production=not is_dev,
)
