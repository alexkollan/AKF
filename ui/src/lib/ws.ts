import type { ClientCommand, ServerEvent } from "@/lib/protocol";
import { useEngineStore } from "@/store/engineStore";

const DEFAULT_URL = "ws://127.0.0.1:8777/ws";
const RECONNECT_DELAY_MS = 1500;

/** The one live socket. Anything else that fires a callback is stale and is ignored. */
let socket: WebSocket | null = null;
let reconnectTimer: number | null = null;

/**
 * Keep one WebSocket to the engine open, reconnecting while the engine is down.
 *
 * Returns a disposer so React effects can tear it down. Every handler checks that its
 * socket is still the current one: in development StrictMode mounts the effect twice, and
 * the first socket's late `onclose` used to schedule a reconnect next to the second one,
 * leaving two live sockets and every event applied twice.
 */
export function connectEngine(url: string = DEFAULT_URL): () => void {
  open(url);
  return () => {
    if (reconnectTimer !== null) {
      window.clearTimeout(reconnectTimer);
      reconnectTimer = null;
    }
    const closing = socket;
    socket = null; // retire it first, so its onclose knows it was closed on purpose
    closing?.close();
  };
}

function open(url: string): void {
  const next = new WebSocket(url);
  socket = next;

  next.onopen = () => {
    if (socket === next) {
      useEngineStore.getState().setConnected(true);
    }
  };

  next.onmessage = (message) => {
    if (socket !== next) {
      return;
    }
    let event: ServerEvent;
    try {
      event = JSON.parse(message.data as string) as ServerEvent;
    } catch {
      return;
    }
    useEngineStore.getState().applyEvent(event);
  };

  next.onclose = () => {
    if (socket !== next) {
      return; // replaced or disposed: not ours to reconnect
    }
    socket = null;
    useEngineStore.getState().setConnected(false);
    reconnectTimer = window.setTimeout(() => {
      reconnectTimer = null;
      open(url);
    }, RECONNECT_DELAY_MS);
  };
}

/** Fire-and-forget a command; dropped silently while the engine is unreachable. */
export function sendCommand(command: ClientCommand): void {
  if (socket?.readyState !== WebSocket.OPEN) {
    return;
  }
  socket.send(JSON.stringify(command));
}
