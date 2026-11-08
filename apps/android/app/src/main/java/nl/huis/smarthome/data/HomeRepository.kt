package nl.huis.smarthome.data

import android.content.Context
import android.os.Build
import androidx.glance.appwidget.updateAll
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharedFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import nl.huis.smarthome.core.Alert
import nl.huis.smarthome.core.ApiClient
import nl.huis.smarthome.core.ApiException
import nl.huis.smarthome.core.EnergySummary
import nl.huis.smarthome.core.GroupCommand
import nl.huis.smarthome.core.LightGroup
import nl.huis.smarthome.core.Overview
import nl.huis.smarthome.core.PendingCommands
import nl.huis.smarthome.core.RealtimeClient
import nl.huis.smarthome.core.RealtimeEvent
import nl.huis.smarthome.core.RealtimeState
import nl.huis.smarthome.core.Route
import nl.huis.smarthome.core.RouteKind
import nl.huis.smarthome.core.RouteSelector
import nl.huis.smarthome.core.Setpoint
import nl.huis.smarthome.core.UrlTools
import nl.huis.smarthome.core.Zone
import nl.huis.smarthome.core.ZoneCommand
import nl.huis.smarthome.widget.HomeWidget
import okhttp3.OkHttpClient
import java.io.IOException
import java.time.Instant
import java.util.concurrent.TimeUnit

data class HomeState(
    val pairing: Pairing? = null,
    val overview: Overview? = null,
    /** When [overview] was last confirmed by the server. */
    val updatedAt: Instant? = null,
    val route: Route? = null,
    /** True while the last request to the server succeeded. */
    val online: Boolean = false,
    val realtime: RealtimeState = RealtimeState.DISCONNECTED,
    val loading: Boolean = false,
    /** Setpoints the user is still adjusting (debounced before sending). */
    val drafts: Map<String, Double> = emptyMap(),
    /** Zones with a command sent but not yet confirmed by readback. */
    val pending: Set<String> = emptySet(),
    val energy: EnergySummary? = null,
    val needsRepair: Boolean = false,
) {
    val paired: Boolean get() = pairing != null
    val controlsEnabled: Boolean get() = online && !needsRepair
}

/**
 * Single source of truth for the UI, widget and background worker.
 * Talks to the server through the LAN address when reachable, else through the tunnel.
 */
class HomeRepository(private val context: Context, val store: SecureStore) {
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)

    val http: OkHttpClient = OkHttpClient.Builder()
        .connectTimeout(5, TimeUnit.SECONDS)
        .readTimeout(15, TimeUnit.SECONDS)
        .pingInterval(25, TimeUnit.SECONDS)
        .build()

    private val lanProbe = http.newBuilder()
        .connectTimeout(1500, TimeUnit.MILLISECONDS)
        .callTimeout(2500, TimeUnit.MILLISECONDS)
        .build()

    private val remoteProbe = http.newBuilder()
        .callTimeout(8, TimeUnit.SECONDS)
        .build()

    private val _state = MutableStateFlow(HomeState())
    val state: StateFlow<HomeState> = _state.asStateFlow()

    private val _messages = MutableSharedFlow<String>(extraBufferCapacity = 8)
    val messages: SharedFlow<String> = _messages

    val api = ApiClient(http, { _state.value.route?.baseUrl }, { store.credentials() })

    private val realtime = RealtimeClient(
        http = http,
        scope = scope,
        urlProvider = { ensureRoute()?.let { UrlTools.webSocketUrl(it.baseUrl, _state.value.pairing?.homeId) } },
        credentials = { store.credentials() },
    )

    private val pending = PendingCommands()
    private val routeMutex = Mutex()
    private val draftJobs = mutableMapOf<String, Job>()
    private var loopJob: Job? = null
    private var eventsJob: Job? = null
    private var stateJob: Job? = null
    private var widgetJob: Job? = null

    init {
        val pairing = store.pairing()
        val cached = OverviewCache.read(context)
        _state.value = HomeState(
            pairing = pairing,
            overview = cached?.overview,
            updatedAt = cached?.savedAt?.let { runCatching { Instant.parse(it) }.getOrNull() },
        )
    }

    // ------------------------------------------------------------------
    // Lifecycle: started while the app is in the foreground.
    // ------------------------------------------------------------------

    fun start() {
        if (!_state.value.paired || loopJob?.isActive == true) return
        realtime.start()
        eventsJob = scope.launch { realtime.events.collect { handle(it) } }
        stateJob = scope.launch { realtime.state.collect { s -> _state.update { it.copy(realtime = s) } } }
        loopJob = scope.launch {
            while (isActive) {
                refresh()
                // Realtime keeps us current; poll slowly as a safety net, faster when the socket is down.
                delay(if (_state.value.realtime == RealtimeState.CONNECTED) 120_000 else 20_000)
            }
        }
    }

    fun stop() {
        loopJob?.cancel()
        eventsJob?.cancel()
        stateJob?.cancel()
        loopJob = null
        eventsJob = null
        realtime.stop()
    }

    // ------------------------------------------------------------------
    // Route selection.
    // ------------------------------------------------------------------

    suspend fun ensureRoute(): Route? = routeMutex.withLock {
        _state.value.route?.let { return@withLock it }
        val p = _state.value.pairing ?: return@withLock null
        val route = RouteSelector.select(p.lanUrl, p.remoteUrl) { base, kind ->
            probe(base, kind)
        }
        _state.update { it.copy(route = route, online = route != null && it.online) }
        route
    }

    private suspend fun probe(base: String, kind: RouteKind): Boolean = try {
        api.health(base, if (kind == RouteKind.LAN) lanProbe else remoteProbe)
        true
    } catch (e: IOException) {
        false
    }

    private fun dropRoute() {
        _state.update { it.copy(route = null, online = false) }
    }

    // ------------------------------------------------------------------
    // Data.
    // ------------------------------------------------------------------

    suspend fun refresh() {
        val p = _state.value.pairing ?: return
        _state.update { it.copy(loading = true) }
        try {
            ensureRoute() ?: run {
                _state.update { it.copy(online = false) }
                return
            }
            val homeId = p.homeId ?: api.homes().firstOrNull()?.also { h ->
                store.updateHome(h.id, h.name)
                _state.update { it.copy(pairing = store.pairing()) }
            }?.id ?: return
            val overview = api.overview(homeId)
            val now = Instant.now()
            _state.update { it.copy(overview = overview, updatedAt = now, online = true, needsRepair = false) }
            OverviewCache.write(context, CachedOverview(now.toString(), overview))
            scheduleWidgetUpdate()
        } catch (e: ApiException) {
            if (e.isAuthError) {
                _state.update { it.copy(needsRepair = true, online = false) }
            } else if (e.code == "no_route") {
                dropRoute()
            } else {
                _messages.tryEmit(e.message)
            }
        } catch (e: IOException) {
            dropRoute()
            realtime.reconnectNow()
        } finally {
            _state.update { it.copy(loading = false) }
        }
    }

    suspend fun loadEnergy(period: String = "day") {
        val homeId = _state.value.pairing?.homeId ?: return
        try {
            ensureRoute() ?: return
            val e = api.energySummary(homeId, period)
            _state.update { it.copy(energy = e) }
        } catch (e: ApiException) {
            _messages.tryEmit(e.message)
        } catch (e: IOException) {
            dropRoute()
        }
    }

    private fun handle(event: RealtimeEvent) {
        when (event) {
            RealtimeEvent.Connected -> scope.launch { refresh() }
            is RealtimeEvent.ZoneUpdated -> {
                val confirmed = pending.onZoneUpdate(event.zone)
                updateOverview { o -> o.copy(zones = o.zones.map { if (it.id == event.zone.id) event.zone else it }) }
                if (confirmed) _state.update { it.copy(pending = it.pending - event.zone.id) }
            }
            is RealtimeEvent.LightsUpdated -> updateOverview { o ->
                o.copy(lights = o.lights.map { if (it.id == event.group.id) event.group else it })
            }
            is RealtimeEvent.EnergyLive -> updateOverview { it.copy(powerNow = event.power) }
            is RealtimeEvent.AlertCreated -> updateOverview { o ->
                o.copy(alerts = listOf(event.alert) + o.alerts.filterNot { it.id == event.alert.id })
            }
            is RealtimeEvent.StatusUpdated -> updateOverview { it.copy(status = event.status) }
        }
    }

    private fun updateOverview(transform: (Overview) -> Overview) {
        val now = Instant.now()
        _state.update { s -> s.overview?.let { s.copy(overview = transform(it), updatedAt = now, online = true) } ?: s }
        _state.value.overview?.let { OverviewCache.write(context, CachedOverview(now.toString(), it)) }
        scheduleWidgetUpdate()
    }

    private fun scheduleWidgetUpdate() {
        if (widgetJob?.isActive == true) return
        widgetJob = scope.launch {
            delay(5_000) // coalesce bursts of realtime updates
            runCatching { HomeWidget().updateAll(context) }
        }
    }

    // ------------------------------------------------------------------
    // Commands.
    // ------------------------------------------------------------------

    /** +/- buttons: updates the draft immediately, sends after the user stops tapping. */
    fun nudgeTarget(zone: Zone, delta: Double) {
        if (!_state.value.controlsEnabled) return
        val current = _state.value.drafts[zone.id] ?: zone.targetC
        val next = Setpoint.step(current, delta, zone.capabilities)
        _state.update { it.copy(drafts = it.drafts + (zone.id to next)) }
        draftJobs[zone.id]?.cancel()
        draftJobs[zone.id] = scope.launch {
            delay(800)
            sendZoneCommand(zone.id, ZoneCommand(targetC = next))
        }
    }

    fun setMode(zone: Zone, mode: String) {
        if (!_state.value.controlsEnabled) return
        scope.launch { sendZoneCommand(zone.id, ZoneCommand(mode = mode)) }
    }

    fun overrideFor(zone: Zone, minutes: Int) {
        if (!_state.value.controlsEnabled) return
        val target = _state.value.drafts[zone.id] ?: zone.targetC
        scope.launch { sendZoneCommand(zone.id, ZoneCommand(targetC = target, overrideMinutes = minutes)) }
    }

    fun resume(zone: Zone) {
        if (!_state.value.controlsEnabled) return
        scope.launch {
            try {
                val updated = api.resume(zone.id)
                updateOverview { o -> o.copy(zones = o.zones.map { if (it.id == updated.id) updated else it }) }
            } catch (e: ApiException) {
                _messages.tryEmit(e.message)
            } catch (e: IOException) {
                dropRoute()
                _messages.tryEmit("Geen verbinding met je huis.")
            }
        }
    }

    private suspend fun sendZoneCommand(zoneId: String, command: ZoneCommand) {
        try {
            api.command(zoneId, command)
            pending.sent(zoneId, command.targetC, command.mode)
            _state.update { it.copy(pending = it.pending + zoneId, drafts = it.drafts - zoneId) }
            // Show the requested target until the readback confirms it.
            updateOverview { o ->
                o.copy(zones = o.zones.map { z ->
                    if (z.id == zoneId) z.copy(targetC = command.targetC ?: z.targetC, mode = command.mode ?: z.mode) else z
                })
            }
            // Fallback when no realtime confirmation arrives.
            scope.launch {
                delay(31_000)
                if (!pending.isPending(zoneId)) {
                    _state.update { it.copy(pending = it.pending - zoneId) }
                    refresh()
                }
            }
        } catch (e: ApiException) {
            _state.update { it.copy(drafts = it.drafts - zoneId) }
            _messages.tryEmit(e.message)
            refresh()
        } catch (e: IOException) {
            _state.update { it.copy(drafts = it.drafts - zoneId) }
            dropRoute()
            _messages.tryEmit("Niet verzonden: geen verbinding met je huis.")
        }
    }

    fun toggleGroup(group: LightGroup) {
        if (!_state.value.controlsEnabled || !group.reachable) return
        scope.launch {
            try {
                api.setGroup(group.id, GroupCommand(on = !group.on))
            } catch (e: ApiException) {
                _messages.tryEmit(e.message)
            } catch (e: IOException) {
                dropRoute()
                _messages.tryEmit("Niet verzonden: geen verbinding met je huis.")
            }
        }
    }

    fun acknowledge(alert: Alert) {
        scope.launch {
            try {
                api.ack(alert.id)
                updateOverview { o -> o.copy(alerts = o.alerts.filterNot { it.id == alert.id }) }
            } catch (e: ApiException) {
                _messages.tryEmit(e.message)
            } catch (e: IOException) {
                dropRoute()
            }
        }
    }

    // ------------------------------------------------------------------
    // Pairing.
    // ------------------------------------------------------------------

    /** Tries the LAN address, then the remote one. Returns an error message or null on success. */
    suspend fun pair(lanUrl: String?, remoteUrl: String?, code: String): String? {
        val deviceName = listOfNotNull(Build.MANUFACTURER, Build.MODEL).joinToString(" ").ifBlank { "Android" }
        val candidates = listOfNotNull(lanUrl?.let { it to RouteKind.LAN }, remoteUrl?.let { it to RouteKind.REMOTE })
        var lastError = "De server is niet bereikbaar op dit adres."
        for ((base, kind) in candidates) {
            try {
                val r = api.redeem(base, code, deviceName)
                val pairing = Pairing(
                    token = r.token,
                    lanUrl = r.server.lanUrl?.let { UrlTools.normalizeBaseUrl(it) } ?: lanUrl,
                    remoteUrl = r.server.remoteUrl?.let { UrlTools.normalizeBaseUrl(it) } ?: remoteUrl,
                    homeId = r.server.homeId,
                    homeName = r.server.homeName,
                    username = r.user.username,
                    role = r.user.role,
                )
                store.savePairing(pairing)
                _state.value = HomeState(pairing = pairing, route = Route(kind, base), online = true)
                start()
                return null
            } catch (e: ApiException) {
                // A real answer from the server (e.g. expired code): don't try the other address.
                return e.message
            } catch (e: IOException) {
                lastError = "De server is niet bereikbaar op dit adres."
            }
        }
        return lastError
    }

    fun unpair() {
        stop()
        store.clear()
        OverviewCache.clear(context)
        _state.value = HomeState()
        scope.launch { runCatching { HomeWidget().updateAll(context) } }
    }

    fun saveCfAccess(id: String?, secret: String?) {
        store.saveCfAccess(id, secret)
        dropRoute()
        realtime.reconnectNow()
        scope.launch { refresh() }
    }

    fun retry() {
        dropRoute()
        realtime.reconnectNow()
        scope.launch { refresh() }
    }
}
