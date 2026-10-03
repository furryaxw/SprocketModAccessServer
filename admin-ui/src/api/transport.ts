import {useLocale} from "../i18n";
import {logger} from "./logger";

export type TransportEnvelope = {
    action: string;
    node: string;
    data?: Record<string, unknown>;
    headers?: Record<string, string>;
};

export type TransportResponse = {
    request_id?: string;
    ok: boolean;
    data?: Record<string, unknown>;
    error?: { code?: string; message?: string };
    kind?: string;
    node?: string;
};

export type TransportConnectionState =
    | "idle"
    | "connecting"
    | "connected"
    | "disconnected";

const tokenKey = "sprocket.access.session";

export function getSessionToken() {
    return localStorage.getItem(tokenKey);
}

export function setSessionToken(token: string | null) {
    if (token) {
        localStorage.setItem(tokenKey, token);
        logger.debug("session.token.saved", {tokenPresent: true});
    } else {
        localStorage.removeItem(tokenKey);
        logger.debug("session.token.cleared");
    }
}

export class WsTransport {
    private socket: WebSocket | null = null;
    private connectionPromise: Promise<void> | null = null;
    private pending = new Map<
        string,
        {
            resolve: (value: TransportResponse) => void;
            reject: (reason: Error) => void;
        }
    >();
    private listeners = new Set<(message: TransportResponse) => void>();
    private statusListeners = new Set<
        (status: TransportConnectionState) => void
    >();
    private status: TransportConnectionState = "idle";
    private teamId: string | null = null;

    get selectedTeamId() {
        return this.teamId;
    }

    get connectionState() {
        return this.status;
    }

    private setConnectionState(status: TransportConnectionState) {
        if (this.status === status) return;
        this.status = status;
        this.statusListeners.forEach((listener) => listener(status));
    }

    async connect() {
        if (this.socket?.readyState === WebSocket.OPEN) {
            this.setConnectionState("connected");
            logger.debug("ws.connect.reused");
            return;
        }
        if (this.connectionPromise) {
            logger.debug("ws.connect.waiting");
            return this.connectionPromise;
        }

        const configuredOrigin = String(
            import.meta.env.VITE_API_ORIGIN ?? "",
        ).trim();
        const parsedOrigin = new URL(configuredOrigin || window.location.origin);
        const protocol = parsedOrigin.protocol === "https:" ? "wss:" : "ws:";
        const token = getSessionToken();
        this.setConnectionState("connecting");
        logger.info("ws.connect.start", {authenticated: Boolean(token)});
        const socket = new WebSocket(
            `${protocol}//${parsedOrigin.host}/ws${
                token ? `?access_token=${encodeURIComponent(token)}` : ""
            }`,
        );
        this.socket = socket;

        socket.onmessage = (event) => {
            let response: TransportResponse;
            try {
                response = JSON.parse(event.data) as TransportResponse;
            } catch {
                logger.warn("ws.message.invalid_json");
                return;
            }
            logger.debug("ws.message", {
                requestId: response.request_id,
                kind: response.kind,
                ok: response.ok,
            });
            this.listeners.forEach((listener) => listener(response));
            if (response.request_id && this.pending.has(response.request_id)) {
                const pending = this.pending.get(response.request_id)!;
                this.pending.delete(response.request_id);
                pending.resolve(response);
            }
        };

        const connectionPromise = new Promise<void>((resolve, reject) => {
            let opened = false;
            const timeout = window.setTimeout(
                () => {
                    logger.warn("ws.connect.timeout");
                    socket.close();
                    reject(new Error(useLocale().t("common.websocketConnectionTimedOut")));
                },
                10000,
            );
            socket.onopen = () => {
                opened = true;
                window.clearTimeout(timeout);
                logger.info("ws.connect.open");
                if (this.socket === socket) {
                    this.setConnectionState("connected");
                }
                resolve();
            };
            socket.onerror = () => {
                logger.error("ws.connect.error");
                if (this.socket === socket) {
                    this.setConnectionState("disconnected");
                }
                reject(new Error(useLocale().t("common.websocketConnectionFailed")));
            };
            socket.onclose = () => {
                window.clearTimeout(timeout);
                logger.warn("ws.connect.close");
                const isCurrentSocket = this.socket === socket;
                if (isCurrentSocket) {
                    this.setConnectionState("disconnected");
                    this.socket = null;
                }
                if (!opened) reject(new Error(useLocale().t("common.websocketConnectionClosed")));
                const pending = [...this.pending.values()];
                this.pending.clear();
                pending.forEach(({reject: rejectPending}) =>
                    rejectPending(new Error(useLocale().t("common.websocketConnectionClosed"))),
                );
            };
        });
        this.connectionPromise = connectionPromise;
        try {
            await connectionPromise;
        } finally {
            if (this.connectionPromise === connectionPromise) {
                this.connectionPromise = null;
            }
        }
    }

    async request(envelope: TransportEnvelope) {
        await this.connect();
        const socket = this.socket;
        if (!socket || socket.readyState !== WebSocket.OPEN) {
            logger.error("ws.request.not_open", {
                action: envelope.action,
                node: envelope.node,
            });
            throw new Error(useLocale().t("common.websocketNotConnected"));
        }
        const requestId = crypto.randomUUID();
            logger.debug("ws.request", {
                requestId,
                action: envelope.action,
                node: envelope.node,
            });
        return new Promise<TransportResponse>((resolve, reject) => {
            this.pending.set(requestId, {resolve, reject});
            try {
                socket.send(JSON.stringify({...envelope, request_id: requestId}));
            } catch (cause) {
                this.pending.delete(requestId);
                logger.error("ws.request.send_failed", {
                    requestId,
                    message: cause instanceof Error ? cause.message : String(cause),
                });
                reject(cause instanceof Error ? cause : new Error(String(cause)));
            }
        });
    }

    async selectTeam(teamId: string) {
        logger.info("workspace.select.start", {teamId});
        const response = await this.request({
            action: "select",
            node: "system.authentication.team_context",
            data: {team_id: teamId},
        });
        if (response.ok) {
            this.teamId = teamId;
            logger.info("workspace.select.success", {teamId});
        } else {
            logger.warn("workspace.select.denied", {
                teamId,
                code: response.error?.code,
            });
        }
        return response;
    }

    clearTeamContext() {
        if (!this.teamId) return;
        logger.info("workspace.select.clear", {teamId: this.teamId});
        this.teamId = null;
        this.close();
    }

    subscribe(listener: (message: TransportResponse) => void) {
        this.listeners.add(listener);
        logger.debug("ws.subscribe", {listenerCount: this.listeners.size});
        return () => {
            this.listeners.delete(listener);
            logger.debug("ws.unsubscribe", {listenerCount: this.listeners.size});
        };
    }

    subscribeStatus(listener: (status: TransportConnectionState) => void) {
        this.statusListeners.add(listener);
        listener(this.status);
        return () => {
            this.statusListeners.delete(listener);
        };
    }

    close() {
        logger.info("ws.close");
        this.setConnectionState("disconnected");
        this.socket?.close();
        this.socket = null;
        const pending = [...this.pending.values()];
        this.pending.clear();
        pending.forEach(({reject}) => reject(new Error(useLocale().t("common.websocketClosed"))));
        this.teamId = null;
        this.connectionPromise = null;
    }
}

export const transport = new WsTransport();
