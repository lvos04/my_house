package nl.huis.smarthome.core

import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Job
import kotlinx.coroutines.channels.BufferOverflow
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharedFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import kotlinx.serialization.builtins.ListSerializer
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.contentOrNull
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener

/** WebSocket close codes sent by the server. */
object CloseCodes {
    /** Log in again (`auth_required`). */
    const val AUTH_REQUIRED = 4401

    /** This phone's token was revoked or expired. */
    const val REVOKED = 4403
}

/** Typed realtime events from `/api/v1/ws`. Unknown types are ignored. */
sealed interface RealtimeEvent {
    data class ZoneUpdated(val zone: Zone) : RealtimeEvent
    data class LightsUpdated(val group: LightGroup) : RealtimeEvent
    data class EnergyLive(val power: LivePower) : RealtimeEvent
    data class AlertCreated(val alert: Alert) : RealtimeEvent
    data class AlertResolved(val id: String) : RealtimeEvent

    /** Replaces `Overview.status` as a whole. */
    data class StatusUpdated(val status: List<StatusItem>) : RealtimeEvent
    data class CommandUpdated(val update: CommandUpdate) : RealtimeEvent

    /** The server could not send the details (too large) or wants a full refetch. */
    data class Refresh(val reason: String?) : RealtimeEvent

    /** Login on the socket accepted; carries the phone's rights. */
    data class HelloReceived(val hello: Hello) : RealtimeEvent

    /** Sent by the server right before it closes the socket. */
    data class ServerError(val error: WsError) : RealtimeEvent

    /**
     * The server closed the socket with 4401 or 4403. On 4403 the client stops reconnecting:
     * the phone is no longer paired ([reason] says why).
     */
    data class Rejected(val closeCode: Int, val code: String?, val reason: UnpairReason?) : RealtimeEvent

    /** Emitted after every successful (re)login: the client should refetch the overview. */
    data object Connected : RealtimeEvent

    companion object {
        fun parse(text: String): RealtimeEvent? {
            val frame = try {
                ShcJson.decodeFromString(WsFrame.serializer(), text)
            } catch (e: IllegalArgumentException) {
                return null
            }
            val data: JsonElement? = frame.data
            return try {
                when (frame.type) {
                    "zone.updated" -> data?.let { ZoneUpdated(ShcJson.decodeFromJsonElement(Zone.serializer(), it)) }
                    "lights.updated" -> data?.let { LightsUpdated(ShcJson.decodeFromJsonElement(LightGroup.serializer(), it)) }
                    "energy.live" -> data?.let { EnergyLive(ShcJson.decodeFromJsonElement(LivePower.serializer(), it)) }
                    "alert.created" -> data?.let { AlertCreated(ShcJson.decodeFromJsonElement(Alert.serializer(), it)) }
                    "alert.resolved" -> idOf(data)?.let { AlertResolved(it) }
                    "status.updated" -> {
                        // A list (current server) or {"status": [...]} / {"items": [...]}.
                        val arr = when (data) {
                            is JsonArray -> data
                            is JsonObject -> data["status"] ?: data["items"]
                            else -> null
                        } ?: return null
                        StatusUpdated(ShcJson.decodeFromJsonElement(ListSerializer(StatusItem.serializer()), arr))
                    }
                    "command.updated" -> data?.let { CommandUpdated(ShcJson.decodeFromJsonElement(CommandUpdate.serializer(), it)) }
                    "refresh" -> Refresh(((data as? JsonObject)?.get("reason") as? JsonPrimitive)?.contentOrNull)
                    "hello" -> HelloReceived(data?.let { ShcJson.decodeFromJsonElement(Hello.serializer(), it) } ?: Hello())
                    "error" -> ServerError(data?.let { ShcJson.decodeFromJsonElement(WsError.serializer(), it) } ?: WsError())
                    else -> null
                }
            } catch (e: IllegalArgumentException) {
                null
            }
        }

        private fun idOf(data: JsonElement?): String? =
            ((data as? JsonObject)?.get("id") as? JsonPrimitive)?.contentOrNull

        fun authFrame(token: String): String =
            JsonObject(mapOf("type" to JsonPrimitive("auth"), "token" to JsonPrimitive(token))).toString()
    }
}

enum class RealtimeState { DISCONNECTED, CONNECTING, CONNECTED }

/**
 * Keeps one WebSocket open while [start]ed, with exponential backoff between attempts.
 * [routeProvider] is asked for every attempt so a route switch (LAN <-> tunnel) is picked up; it
 * only returns identity-checked routes. The token goes in the first frame, only to a verified
 * route; the Cloudflare Access headers only to the remote route.
 */
class RealtimeClient(
    private val http: OkHttpClient,
    private val scope: CoroutineScope,
    private val routeProvider: suspend () -> Route?,
    private val homeId: () -> String?,
    private val credentials: () -> Credentials,
    /** Called when a socket could not be opened on [Route] (network error, 5xx): forget that route. */
    private val onConnectFailure: (Route) -> Unit = {},
    private val backoff: Backoff = Backoff(),
) {
    private val _events = MutableSharedFlow<RealtimeEvent>(extraBufferCapacity = 64, onBufferOverflow = BufferOverflow.DROP_OLDEST)
    val events: SharedFlow<RealtimeEvent> = _events

    private val _state = MutableStateFlow(RealtimeState.DISCONNECTED)
    val state: StateFlow<RealtimeState> = _state.asStateFlow()

    private var job: Job? = null

    @Volatile private var socket: WebSocket? = null

    /** Builds the upgrade request with exactly the headers this route may get. Visible for tests. */
    fun request(route: Route): Request {
        val creds = credentials()
        return Request.Builder().url(UrlTools.webSocketUrl(route.baseUrl, homeId())).apply {
            if (route.kind == RouteKind.REMOTE && creds.hasCfAccess) {
                header("CF-Access-Client-Id", creds.cfAccessClientId!!)
                header("CF-Access-Client-Secret", creds.cfAccessClientSecret!!)
            }
        }.build()
    }

    fun start() {
        if (job?.isActive == true) return
        job = scope.launch {
            while (isActive) {
                val route = routeProvider()
                if (route == null || !route.verified) {
                    _state.value = RealtimeState.DISCONNECTED
                    delay(backoff.nextDelayMs())
                    continue
                }
                _state.value = RealtimeState.CONNECTING
                val closed = CompletableDeferred<Int>()
                var helloSeen = false
                var lastError: WsError? = null
                val creds = credentials()
                socket = http.newWebSocket(request(route), object : WebSocketListener() {
                    override fun onOpen(webSocket: WebSocket, response: Response) {
                        creds.token?.let { webSocket.send(RealtimeEvent.authFrame(it)) }
                    }

                    override fun onMessage(webSocket: WebSocket, text: String) {
                        val event = RealtimeEvent.parse(text) ?: return
                        when (event) {
                            is RealtimeEvent.HelloReceived -> {
                                // Reset only once the server accepted the login, so a refused
                                // token doesn't cause a tight reconnect loop.
                                helloSeen = true
                                backoff.reset()
                                _state.value = RealtimeState.CONNECTED
                                _events.tryEmit(event)
                                _events.tryEmit(RealtimeEvent.Connected)
                            }
                            is RealtimeEvent.ServerError -> {
                                lastError = event.error
                                _events.tryEmit(event)
                            }
                            else -> _events.tryEmit(event)
                        }
                    }

                    override fun onClosing(webSocket: WebSocket, code: Int, reason: String) {
                        webSocket.close(1000, null)
                        closed.complete(code)
                    }

                    override fun onClosed(webSocket: WebSocket, code: Int, reason: String) {
                        closed.complete(code)
                    }

                    override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) {
                        closed.complete(-1)
                    }
                })
                val code = try {
                    closed.await()
                } finally {
                    socket?.cancel()
                    socket = null
                    _state.value = RealtimeState.DISCONNECTED
                }
                when (code) {
                    CloseCodes.REVOKED -> {
                        val c = lastError?.code
                        _events.tryEmit(RealtimeEvent.Rejected(code, c, UnpairReason.fromCode(c) ?: UnpairReason.REVOKED))
                        return@launch // never retry with a revoked token
                    }
                    CloseCodes.AUTH_REQUIRED -> {
                        // Unknown token, or the login frame was too late: the app checks over HTTP.
                        _events.tryEmit(RealtimeEvent.Rejected(code, lastError?.code, null))
                    }
                    -1 -> if (!helloSeen) onConnectFailure(route)
                }
                delay(backoff.nextDelayMs())
            }
        }
    }

    fun stop() {
        job?.cancel()
        job = null
        socket?.close(1000, null)
        socket = null
        _state.value = RealtimeState.DISCONNECTED
    }

    /** Drop the current socket so the next attempt re-resolves the route. */
    fun reconnectNow() {
        backoff.reset()
        socket?.cancel()
    }

    val running: Boolean get() = job?.isActive == true
}
