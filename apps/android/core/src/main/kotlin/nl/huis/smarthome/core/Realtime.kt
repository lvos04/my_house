package nl.huis.smarthome.core

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
import kotlinx.coroutines.CompletableDeferred
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener

/** Typed realtime events from `/api/v1/ws`. Unknown types are ignored. */
sealed interface RealtimeEvent {
    data class ZoneUpdated(val zone: Zone) : RealtimeEvent
    data class LightsUpdated(val group: LightGroup) : RealtimeEvent
    data class EnergyLive(val power: LivePower) : RealtimeEvent
    data class AlertCreated(val alert: Alert) : RealtimeEvent
    data class StatusUpdated(val status: List<StatusItem>) : RealtimeEvent

    /** Emitted after every (re)connect: the client should refetch the overview. */
    data object Connected : RealtimeEvent

    companion object {
        fun parse(text: String): RealtimeEvent? {
            val frame = try {
                ShcJson.decodeFromString(WsFrame.serializer(), text)
            } catch (e: IllegalArgumentException) {
                return null
            }
            val data = frame.data ?: return null
            return try {
                when (frame.type) {
                    "zone.updated" -> ZoneUpdated(ShcJson.decodeFromJsonElement(Zone.serializer(), data))
                    "lights.updated" -> LightsUpdated(ShcJson.decodeFromJsonElement(LightGroup.serializer(), data))
                    "energy.live" -> EnergyLive(ShcJson.decodeFromJsonElement(LivePower.serializer(), data))
                    "alert.created" -> AlertCreated(ShcJson.decodeFromJsonElement(Alert.serializer(), data))
                    "status.updated" -> {
                        // data may be {"status": [...]} or {"items": [...]}
                        val arr = data["status"] ?: data["items"] ?: return null
                        StatusUpdated(ShcJson.decodeFromJsonElement(kotlinx.serialization.builtins.ListSerializer(StatusItem.serializer()), arr))
                    }
                    else -> null
                }
            } catch (e: IllegalArgumentException) {
                null
            }
        }

        fun authFrame(token: String): String =
            JsonObject(mapOf("type" to JsonPrimitive("auth"), "token" to JsonPrimitive(token))).toString()
    }
}

enum class RealtimeState { DISCONNECTED, CONNECTING, CONNECTED }

/**
 * Keeps one WebSocket open while [start]ed, with exponential backoff between attempts.
 * The URL provider is called for every attempt so a route switch (LAN <-> tunnel) is picked up.
 */
class RealtimeClient(
    private val http: OkHttpClient,
    private val scope: CoroutineScope,
    private val urlProvider: suspend () -> String?,
    private val credentials: () -> Credentials,
    private val backoff: Backoff = Backoff(),
) {
    private val _events = MutableSharedFlow<RealtimeEvent>(extraBufferCapacity = 64, onBufferOverflow = BufferOverflow.DROP_OLDEST)
    val events: SharedFlow<RealtimeEvent> = _events

    private val _state = MutableStateFlow(RealtimeState.DISCONNECTED)
    val state: StateFlow<RealtimeState> = _state.asStateFlow()

    private var job: Job? = null
    @Volatile private var socket: WebSocket? = null

    fun start() {
        if (job?.isActive == true) return
        job = scope.launch {
            while (isActive) {
                val url = urlProvider()
                if (url == null) {
                    _state.value = RealtimeState.DISCONNECTED
                    delay(backoff.nextDelayMs())
                    continue
                }
                _state.value = RealtimeState.CONNECTING
                val closed = CompletableDeferred<Unit>()
                val creds = credentials()
                val req = Request.Builder().url(url).apply {
                    if (!creds.cfAccessClientId.isNullOrBlank() && !creds.cfAccessClientSecret.isNullOrBlank()) {
                        header("CF-Access-Client-Id", creds.cfAccessClientId)
                        header("CF-Access-Client-Secret", creds.cfAccessClientSecret)
                    }
                }.build()
                socket = http.newWebSocket(req, object : WebSocketListener() {
                    override fun onOpen(webSocket: WebSocket, response: Response) {
                        creds.token?.let { webSocket.send(RealtimeEvent.authFrame(it)) }
                        _state.value = RealtimeState.CONNECTED
                        _events.tryEmit(RealtimeEvent.Connected)
                    }

                    override fun onMessage(webSocket: WebSocket, text: String) {
                        // Reset only once the server actually talks to us (auth accepted),
                        // so a rejected token doesn't cause a tight reconnect loop.
                        backoff.reset()
                        RealtimeEvent.parse(text)?.let { _events.tryEmit(it) }
                    }

                    override fun onClosing(webSocket: WebSocket, code: Int, reason: String) {
                        webSocket.close(1000, null)
                    }

                    override fun onClosed(webSocket: WebSocket, code: Int, reason: String) {
                        closed.complete(Unit)
                    }

                    override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) {
                        closed.complete(Unit)
                    }
                })
                try {
                    closed.await()
                } finally {
                    socket?.cancel()
                    socket = null
                    _state.value = RealtimeState.DISCONNECTED
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
}
