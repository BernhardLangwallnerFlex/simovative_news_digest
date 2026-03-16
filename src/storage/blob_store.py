"""Azure Blob Storage helpers — upload/download JSON blobs."""

import json
import logging
import os

logger = logging.getLogger(__name__)


def _get_blob_client(container: str, blob_name: str):
    """Return an Azure BlobClient using the connection string from env."""
    conn_str = os.getenv("AZURE_STORAGE_CONNECTION_STRING")
    if not conn_str:
        raise RuntimeError(
            "AZURE_STORAGE_CONNECTION_STRING is not set in environment"
        )
    from azure.storage.blob import BlobServiceClient

    service = BlobServiceClient.from_connection_string(conn_str)
    return service.get_blob_client(container=container, blob=blob_name)


def download_json(container: str, blob_name: str) -> dict | list | None:
    """Download a JSON blob and return the parsed object.

    Returns None if the blob does not exist (first run).
    """
    from azure.core.exceptions import ResourceNotFoundError

    client = _get_blob_client(container, blob_name)
    try:
        data = client.download_blob().readall()
        return json.loads(data.decode("utf-8"))
    except ResourceNotFoundError:
        logger.info("Blob %s/%s does not exist yet (first run)", container, blob_name)
        return None


def upload_json(container: str, blob_name: str, obj: dict | list) -> None:
    """Serialize obj to JSON and upload, overwriting any existing blob."""
    client = _get_blob_client(container, blob_name)
    payload = json.dumps(obj, ensure_ascii=False, indent=2).encode("utf-8")
    client.upload_blob(payload, overwrite=True)
    logger.info("Uploaded %d bytes to %s/%s", len(payload), container, blob_name)
