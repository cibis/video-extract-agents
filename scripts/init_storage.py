#!/usr/bin/env python3
"""
Initialise Azurite blob storage for local development.
- Creates the 'videos' container

Browser uploads go through the api-gateway blob proxy, so no CORS rules
need to be set on Azurite directly.

Usage:
  python scripts/init_storage.py
"""
import os
from azure.storage.blob import BlobServiceClient

CONNECTION_STRING = os.environ.get(
    "AZURE_STORAGE_CONNECTION_STRING",
    "DefaultEndpointsProtocol=http;AccountName=devstoreaccount1;"
    "AccountKey=Eby8vdM02xNOcqFlqUwJPLlmEtlCDXJ1OUzFT50uSRZ6IFsuFq2UVErCz4I6tq/K1SZFPTOtr/KBHBeksoGMGw==;"
    "BlobEndpoint=http://azurite:10000/devstoreaccount1;",
)
CONTAINER_NAME = os.environ.get("AZURE_STORAGE_CONTAINER_NAME", "videos")


def main() -> None:
    client = BlobServiceClient.from_connection_string(CONNECTION_STRING)

    # Create container with public blob read access (idempotent).
    # Public access lets MCP analysis tools download keyframe images via plain
    # HTTP GET without needing SAS tokens or SDK auth headers.
    container = client.get_container_client(CONTAINER_NAME)
    try:
        container.create_container(public_access="blob")
        print(f"Container '{CONTAINER_NAME}' created with public blob access.")
    except Exception:
        # Container already exists — ensure public access is set.
        try:
            container.set_container_access_policy(signed_identifiers={}, public_access="blob")
            print(f"Container '{CONTAINER_NAME}' already exists; public blob access applied.")
        except Exception as e:
            print(f"Container '{CONTAINER_NAME}' already exists (could not update access: {e}).")


if __name__ == "__main__":
    main()
