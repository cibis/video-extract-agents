import { ServiceBusClient } from '@azure/service-bus';
import { config } from '../config';

let _sbClient: ServiceBusClient | null = null;

export function getServiceBusClient(): ServiceBusClient {
  if (!_sbClient) {
    _sbClient = new ServiceBusClient(config.AZURE_SERVICE_BUS_CONNECTION_STRING);
  }
  return _sbClient;
}

export async function publishJobQueued(payload: {
  jobId: string;
  userId: string;
  videoId: string;
  videoIds?: string[];
  prompt: string;
  sessionId?: string;
  parentJobId?: string;
}): Promise<void> {
  const client = getServiceBusClient();
  const sender = client.createSender('job-queued');
  try {
    await sender.sendMessages({
      body: payload,
      contentType: 'application/json',
    });
  } finally {
    await sender.close();
  }
}

export async function publishVideoUploaded(payload: {
  videoId: string;
  userId: string;
  blobUrl: string;
  sessionId?: string;
}): Promise<void> {
  const client = getServiceBusClient();
  const sender = client.createSender('video-uploaded');
  try {
    await sender.sendMessages({
      body: payload,
      contentType: 'application/json',
    });
  } finally {
    await sender.close();
  }
}
