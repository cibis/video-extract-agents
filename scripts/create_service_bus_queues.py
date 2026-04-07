#!/usr/bin/env python3
"""
Create all Azure Service Bus queues for the Video Extract Platform.
Usage:
  python scripts/create_service_bus_queues.py
"""
import asyncio
import os
from azure.servicebus.management import ServiceBusAdministrationClient

CONNECTION_STRING = os.environ.get("AZURE_SERVICE_BUS_CONNECTION_STRING", "")

QUEUES = [
    "video-uploaded",
    "video-indexed",
    "job-queued",
    "job-completed",
    "job-failed",
]


def create_queues() -> None:
    if not CONNECTION_STRING:
        print("ERROR: AZURE_SERVICE_BUS_CONNECTION_STRING is not set")
        raise SystemExit(1)

    client = ServiceBusAdministrationClient.from_connection_string(CONNECTION_STRING)

    for queue_name in QUEUES:
        try:
            client.create_queue(
                queue_name,
                max_delivery_count=10,
                lock_duration="PT5M",
                default_message_time_to_live="P14D",
            )
            print(f"Created queue: {queue_name}")
        except Exception as exc:
            if "already exists" in str(exc).lower() or "conflict" in str(exc).lower():
                print(f"Queue already exists (skipping): {queue_name}")
            else:
                print(f"Failed to create queue {queue_name}: {exc}")
                raise

    print("All queues ready.")


if __name__ == "__main__":
    create_queues()
