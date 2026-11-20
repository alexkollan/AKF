import type { ClientCommand, ServerEvent } from "@/lib/protocol";
import { useEngineStore } from "@/store/engineStore";

const DEFAULT_URL = "ws://127.0.0.1:8777/ws";
const RECONNECT_DELAY_MS = 1500;

let socket: WebSocket | null = null;
let reconnectTimer: number | null = null;
let closedByUs = false;

/**
 * Keep one WebSocket to the engine open, reconnecting while the engine is down.
 *
 * Returns a disposer so React effects can tear it down in development.
 */
export function connectEngine(url: string = DEFAULT_URL): () => void {
  closedByUs = false;
  open(url);
  return () => {
    closedByUs = true;
    if (reconnectTimer !== null) {
      window.clearTimeout(reconnectTimer);
      reconnectTimer = null;
    }
    socket?.close();
    socket = null;
  };
}

function open(url: string): void {
  const next = new WebSocket(url);
  socket = next;

  next.onopen = () => useEngineStore.getState().setConnected(true);

  next.onmessage = (message) => {
    let event: ServerEvent;
    try {
      event = JSON.parse(message.data as string) as ServerEvent;
    } catch {
      return;
    }
    useEngineStore.getState().applyEvent(event);
  };

  next.onclose = () => {
    useEngineStore.getState().setConnected(false);
    if (closedByUs) {
      return;
    }
    reconnectTimer = window.setTimeout(() => open(url), RECONNECT_DELAY_MS);
  };
}

/** Fire-and-forget a command; dropped silently while the engine is unreachable. */
export function sendCommand(command: ClientCommand): void {
  if (socket?.readyState !== WebSocket.OPEN) {
    return;
  }
  socket.send(JSON.stringify(command));
}
