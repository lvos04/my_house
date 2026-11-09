package nl.huis.smarthome.data

import android.content.Context
import android.os.Build
import androidx.glance.appwidget.updateAll
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.TimeoutCancellationException
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharedFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import kotlinx.coroutines.withTimeout
import nl.huis.smarthome.core.Addresses
import nl.huis.smarthome.core.AddressSync
import nl.huis.smarthome.core.Alert
import nl.huis.smarthome.core.ApiClient
import nl.huis.smarthome.core.ApiException
import nl.huis.smarthome.core.CommandTarget
import nl.huis.smarthome.core.CommandTracker
import nl.huis.smarthome.core.ConnectionStatus
import nl.huis.smarthome.core.ConnectionText
import nl.huis.smarthome.core.Debouncer
import nl.huis.smarthome.core.EnergySummary
import nl.huis.smarthome.core.FailureKind
import nl.huis.smarthome.core.Failures
import nl.huis.smarthome.core.GroupCommand
import nl.huis.smarthome.core.HostRules
import nl.huis.smarthome.core.IdentityVerdict
import nl.huis.smarthome.core.LightGroup
import nl.huis.smarthome.core.NoRouteException
import nl.huis.smarthome.core.Overview
import nl.huis.smarthome.core.PairParse
import nl.huis.smarthome.core.Probe
import nl.huis.smarthome.core.RealtimeClient
import nl.huis.smarthome.core.RealtimeEvent
import nl.huis.smarthome.core.RealtimeState
import nl.huis.smarthome.core.Route
import nl.huis.smarthome.core.RouteChoice
import nl.huis.smarthome.core.RouteKind
import nl.huis.smarthome.core.RouteManager
import nl.huis.smarthome.core.Routed
import nl.huis.smarthome.core.ServerIdentityCheck
import nl.huis.smarthome.core.ServerUrls
import nl.huis.smarthome.core.Setpoint
import nl.huis.smarthome.core.TokenLifetime
import nl.huis.smarthome.core.TrackedCommand
import nl.huis.smarthome.core.UnitLink
import nl.huis.smarthome.core.UnpairReason
import nl.huis.smarthome.core.UrlTools
import nl.huis.smarthome.core.Zone
import nl.huis.smarthome.core.ZoneCommand
import nl.huis.smarthome.widget.HomeWidget
import nl.huis.smarthome.work.AlertNotifier
import nl.huis.smarthome.work.AlertWorker
import okhttp3.OkHttpClient
import java.time.Duration
import java.time.Instant
import java.util.concurrent.TimeUnit

data class HomeState(
    val pairing: Pairing? = null,
    val overview: Overview? = null,
    /** When [overview] was last confirmed by the server. */
    val updatedAt: Instant? = null,
    /** The identity-checked route in use; null while none is known. */
    val route: Route? = null,
    /** True while the last request to the server succeeded. */
    val online: Boolean = false,
    /** What the connection banner should say (offline, starting, wrong server, unpaired …). */
    val connection: ConnectionStatus = ConnectionStatus.CONNECTING,
    /** Dutch explanation for the banner, null when online. */
    val connectionDetail: String? = null,
    val realtime: RealtimeState = RealtimeState.DISCONNECTED,
    val loading: Boolean = false,
    /** Setpoint shown per zone while the user adjusts it and until the command is final. */
    val drafts: Map<String, Double> = emptyMap(),
    /** Zones/groups with a command in flight (kept for older UI code; see [commands]). */
    val pending: Set<String> = emptySet(),
    /** Last command per zone id or light-group id, with its status from `command.updated`. */
    val commands: Map<String, TrackedCommand> = emptyMap(),
    val energy: EnergySummary? = null,
    /** Set when the server no longer accepts this phone: show "Deze telefoon is niet meer gekoppeld". */
    val unpaired: UnpairReason? = null,
    /** The encrypted store was damaged and wiped at start-up; the phone must be paired again. */
    val storeWasReset: Boolean = false,
    /** Alerts waiting for notification permission (shown as soon as it is granted). */
    val notificationsWaiting: Int = 0,
) {
    val paired: Boolean get() = pairing != null

    /** Kept for older UI code: true when the phone must be paired again. */
    val needsRepair: Boolean get() = unpaired != null

    /** May operate climate and lights (admin user and phone role admin/control). */
    val canControl: Boolean
        get() = pairing?.let { it.canControl ?: TokenLifetime.canControl(it.role, it.clientRole) } ?: false

    /** Full management rights (admin user and phone role admin). */
    val isAdmin: Boolean
        get() = pairing?.let { it.isAdmin ?: (it.role == "admin" && (it.clientRole == null || it.clientRole == "admin")) } ?: false

    /** Viewer phone: show values, hide or disable every control. */
    val readOnly: Boolean get() = paired && !canControl

    val wrongServer: Boolean get() = connection == ConnectionStatus.WRONG_SERVER

    val controlsEnabled: Boolean get() = online && unpaired == null && canControl

    /** Target to show for [zone]: the draft, else the target of a command in flight, else the readback. */
    fun shownTarget(zone: Zone): Double? =
        drafts[zone.id] ?: commands[zone.id]?.takeIf { it.inFlight }?.requested?.targetC ?: zone.targetC

    fun command(targetId: String): TrackedCommand? = commands[targetId]
}

/** Outcome of "Ontkoppelen". */
enum class UnpairResult {
    /** The server revoked the token (or it was already dead). */
    REVOKED_ON_SERVER,

    /** Only this phone forgot it: the server could not be reached; revoke it in the web app. */
    LOCAL_ONLY,
}

/**
 * Single source of truth for the UI, widget and background worker.
 *
 * Talks to the server through the home address when it answers as the paired server, else through
 * the tunnel (verbeterlijst 17/37). Every request goes through [routed]: on a network or server
 * error the route is forgotten and picked again once. The route is also forgotten on every app
 * start and network change.
 */
class HomeRepository(private val context: Context, val store: SecureStore) {
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)

    val http: OkHttpClient = OkHttpClient.Builder()
        .connectTimeout(5, TimeUnit.SECONDS)
        .readTimeout(15, TimeUnit.SECONDS)
        .pingInterval(25, TimeUnit.SECONDS)
        // A redirect is never our API (Cloudflare Access login, wifi login page); and following it
        // would carry the Cloudflare headers to another host.
        .followRedirects(false)
        .followSslRedirects(false)
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

    /** One-off Dutch messages for a snackbar. Connection problems only appear when the status changes. */
    val messages: SharedFlow<String> = _messages

    val api = ApiClient(http, { store.credentials() })

    private val routes = RouteManager(
        target = { _state.value.pairing?.let { RouteManager.RouteTarget(it.lanUrl, it.remoteUrl, it.serverId) } },
        onChoice = ::onRouteChoice,
    ) { base, kind -> probe(base, kind) }

    private val realtime = RealtimeClient(
        http = http,
        scope = scope,
        routeProvider = { if (_state.value.unpaired == null) routes.choose().route else null },
        homeId = { _state.value.pairing?.homeId },
        credentials = { store.credentials() },
        onConnectFailure = { routes.forget(it) },
    )

    private val tracker = CommandTracker()
    private val sending = java.util.Collections.synchronizedSet(mutableSetOf<String>())
    private val setpoints = Debouncer<String, Double>(scope, 800) { zoneId, value ->
        sendZoneCommand(zoneId, ZoneCommand(targetC = value))
    }
    private val brightness = Debouncer<String, Int>(scope, 500) { groupId, pct ->
        sendLightCommand(groupId, GroupCommand(brightness = pct))
    }
    private val networkChange = Debouncer<Unit, Unit>(scope, 1000) { _, _ -> handleNetworkChange() }

    @Volatile private var started = false

    @Volatile private var sessionChecked = false

    @Volatile private var lastRefreshAttempt: Instant? = null

    /** When the token was last replaced: a request still carrying the old one gets 401 for a moment. */
    @Volatile private var tokenRotatedAt: Instant? = null

    /** Last error message from the poll, so the same failure is not repeated every 20 s. */
    @Volatile private var lastPollError: String? = null
    private var loopJob: Job? = null
    private var eventsJob: Job? = null
    private var stateJob: Job? = null
    private var tickJob: Job? = null
    private var persistJob: Job? = null

    init {
        val cached = OverviewCache.read(context)
        _state.value = HomeState(
            pairing = store.pairing(),
            overview = cached?.overview,
            updatedAt = cached?.savedAt?.let { runCatching { Instant.parse(it) }.getOrNull() },
            unpaired = store.unpairedReason(),
            storeWasReset = store.wasReset,
            notificationsWaiting = AlertWorker.waitingCount(context),
        ).let { s -> if (s.unpaired != null) s.copy(connection = ConnectionStatus.UNPAIRED, connectionDetail = s.unpaired.message) else s }
    }

    // ------------------------------------------------------------------
    // Lifecycle: started while the app is in the foreground.
    // ------------------------------------------------------------------

    fun start() = start(forgetRoute = true)

    private fun start(forgetRoute: Boolean) {
        val s = _state.value
        if (!s.paired || s.unpaired != null || loopJob?.isActive == true) return
        started = true
        sessionChecked = false
        // Every time the app opens: try the home address first again.
        if (forgetRoute) routes.forget()
        realtime.start()
        eventsJob = scope.launch { realtime.events.collect { handle(it) } }
        stateJob = scope.launch { realtime.state.collect { rs -> _state.update { it.copy(realtime = rs) } } }
        tickJob = scope.launch {
            while (isActive) {
                delay(2_000)
                tick()
            }
        }
        loopJob = scope.launch {
            while (isActive) {
                refresh()
                // Realtime keeps us current; poll slowly as a safety net, faster when the socket is down.
                delay(if (_state.value.realtime == RealtimeState.CONNECTED) 120_000 else 20_000)
            }
        }
    }

    fun stop() {
        started = false
        loopJob?.cancel()
        eventsJob?.cancel()
        stateJob?.cancel()
        tickJob?.cancel()
        loopJob = null
        eventsJob = null
        stateJob = null
        tickJob = null
        realtime.stop()
        // Values still being debounced are sent anyway: the user expects them to arrive.
    }

    /** "Opnieuw proberen": forget the route and try again (home address first). */
    fun retry() {
        routes.forget()
        realtime.reconnectNow()
        scope.launch { refresh() }
    }

    /** Called by [NetworkWatcher] on every network switch. */
    fun onNetworkChanged() {
        networkChange.submit(Unit, Unit)
    }

    private fun handleNetworkChange() {
        if (_state.value.unpaired != null) return
        routes.forget()
        _state.update { it.copy(route = null) }
        if (started) {
            realtime.reconnectNow()
            scope.launch { refresh() }
        }
    }

    // ------------------------------------------------------------------
    // Routes.
    // ------------------------------------------------------------------

    /**
     * Runs [block] on the identity-checked route; on a network or server error it forgets the
     * route, picks again once (home first, then tunnel) and retries once. Used by the UI, the
     * background worker and the widget refresh.
     */
    suspend fun <T> routed(block: suspend (Route) -> T): T {
        if (_state.value.unpaired != null) throw NoRouteException(null)
        return Routed.call(routes, block)
    }

    /** The route in use, or a freshly picked one; null when no address answers as the paired server. */
    suspend fun ensureRoute(): Route? = if (_state.value.unpaired != null) null else routes.choose().route

    private suspend fun probe(base: String, kind: RouteKind): Probe = try {
        Probe.Answered(api.identity(base, kind, if (kind == RouteKind.LAN) lanProbe else remoteProbe).serverId)
    } catch (e: CancellationException) {
        throw e
    } catch (e: Exception) {
        Probe.fromFailure(e)
    }

    private fun onRouteChoice(choice: RouteChoice) {
        choice.learnedServerId?.let { id ->
            // Paired before server ids existed: learned over https from the tunnel address.
            store.updateServerId(id)
            reloadPairing()
        }
        when {
            choice.route != null -> _state.update { it.copy(route = choice.route) }
            choice.unverifiable -> markUnpaired(UnpairReason.UPGRADE)
            choice.wrongServer -> setConnection(ConnectionStatus.WRONG_SERVER)
            choice.blockedReason != null -> setConnection(ConnectionStatus.OFFLINE, choice.blockedReason)
        }
    }

    // ------------------------------------------------------------------
    // Data.
    // ------------------------------------------------------------------

    /** Fetches the overview (and once per session addresses, rights and the token). True on success. */
    suspend fun refresh(): Boolean {
        val p = _state.value.pairing ?: return false
        if (_state.value.unpaired != null) return false
        _state.update { it.copy(loading = true) }
        try {
            val homeId = p.homeId ?: routed { api.homes(it) }.firstOrNull()?.let { h ->
                store.updateHome(h.id, h.name)
                reloadPairing()
                h.id
            }
            if (homeId == null) {
                setConnection(ConnectionStatus.ONLINE)
                _messages.tryEmit("Er is nog geen woning ingesteld. Doe dit in de web-app.")
                return false
            }
            val overview = routed { api.overview(it, homeId) }
            val now = Instant.now()
            overview.zones.forEach { z -> tracker.onZone(z, now)?.let(::reportCommand) }
            _state.update { it.copy(overview = overview, updatedAt = now, route = routes.route) }
            setConnection(ConnectionStatus.ONLINE)
            syncCommands()
            persist(immediately = true)
            lastPollError = null
            if (!sessionChecked) {
                sessionChecked = true
                checkSession()
            }
            return true
        } catch (e: CancellationException) {
            throw e
        } catch (e: Exception) {
            // A connection problem shows as the banner; an API error once, not on every poll.
            val repeated = e.message == lastPollError
            lastPollError = e.message
            onFailure(e, quiet = repeated)
            return false
        } finally {
            _state.update { it.copy(loading = false) }
        }
    }

    /**
     * Once per session: current addresses from the server, this phone's rights, and the token
     * refresh when it expires within 30 days. Failures here never change the connection state.
     */
    private suspend fun checkSession() {
        // Addresses (the NAS may have a new IP address, or the tunnel a new name).
        try {
            val s = routed { api.settings(it) }
            val p = _state.value.pairing ?: return
            AddressSync.merge(Addresses(p.lanUrl, p.remoteUrl), s.lanUrl, s.remoteUrl)?.let { a ->
                store.updateAddresses(a.lanUrl, a.remoteUrl)
                reloadPairing()
            }
        } catch (e: CancellationException) {
            throw e
        } catch (e: Exception) {
            if (unpairedBy(e)) return
        }
        // Rights: read-only for viewer phones.
        try {
            val me = routed { api.me(it) }
            store.updateAccess(me.canControl, me.isAdminFlag, me.clientRole, me.role)
            reloadPairing()
        } catch (e: CancellationException) {
            throw e
        } catch (e: Exception) {
            if (unpairedBy(e)) return
        }
        // Token lifetime.
        try {
            if (_state.value.pairing?.tokenExpiresAt == null) {
                val self = routed { api.clientSelf(it) }
                store.updateExpiry(self.expiresAt)
                if (self.role != null) store.updateAccess(null, null, self.role, null)
                reloadPairing()
            }
            refreshTokenIfDue()
        } catch (e: CancellationException) {
            throw e
        } catch (e: Exception) {
            unpairedBy(e)
        }
    }

    private suspend fun refreshTokenIfDue() {
        val expires = _state.value.pairing?.tokenExpiresAt?.let { runCatching { Instant.parse(it) }.getOrNull() }
        val now = Instant.now()
        if (!TokenLifetime.shouldRefresh(expires, now, lastRefreshAttempt)) return
        lastRefreshAttempt = now
        // Not through [routed]: a retry after a lost answer would send the token that just died.
        val route = routes.choose().route ?: return
        val fresh = api.refreshToken(route)
        // The old token stopped working: store the new one before anything else uses the store.
        store.saveToken(fresh.token, fresh.expiresAt)
        tokenRotatedAt = Instant.now()
        reloadPairing()
    }

    suspend fun loadEnergy(period: String = "day", date: String? = null) {
        val homeId = _state.value.pairing?.homeId ?: return
        try {
            val e = routed { api.energySummary(it, homeId, period, date) }
            _state.update { it.copy(energy = e) }
        } catch (e: CancellationException) {
            throw e
        } catch (e: Exception) {
            onFailure(e)
        }
    }

    private fun handle(event: RealtimeEvent) {
        val now = Instant.now()
        when (event) {
            RealtimeEvent.Connected -> scope.launch { refresh() }
            is RealtimeEvent.HelloReceived -> event.hello.canControl?.let { cc ->
                store.updateAccess(cc, null, event.hello.clientRole, event.hello.user?.role)
                reloadPairing()
            }
            is RealtimeEvent.ZoneUpdated -> {
                updateOverview { o -> o.copy(zones = o.zones.map { if (it.id == event.zone.id) event.zone else it }) }
                tracker.onZone(event.zone, now)?.let(::reportCommand)
                syncCommands()
            }
            is RealtimeEvent.LightsUpdated -> updateOverview { o ->
                o.copy(lights = o.lights.map { if (it.id == event.group.id) event.group else it })
            }
            is RealtimeEvent.EnergyLive -> updateOverview { it.copy(powerNow = event.power) }
            is RealtimeEvent.AlertCreated -> updateOverview { o ->
                o.copy(alerts = listOf(event.alert) + o.alerts.filterNot { it.id == event.alert.id })
            }
            is RealtimeEvent.AlertResolved -> {
                updateOverview { o -> o.copy(alerts = o.alerts.filterNot { it.id == event.id }) }
                AlertNotifier.cancel(context, event.id)
            }
            is RealtimeEvent.StatusUpdated -> updateOverview { it.copy(status = event.status) }
            is RealtimeEvent.CommandUpdated -> {
                tracker.onUpdate(event.update, now)?.let(::reportCommand)
                syncCommands()
            }
            is RealtimeEvent.Refresh -> scope.launch { refresh() }
            is RealtimeEvent.ServerError -> Unit // the close code that follows says what to do
            is RealtimeEvent.Rejected -> {
                val reason = event.reason
                if (reason != null) markUnpaired(reason) else scope.launch { refresh() } // 4401: check over HTTP
            }
        }
    }

    private fun updateOverview(transform: (Overview) -> Overview) {
        val now = Instant.now()
        _state.update { s -> s.overview?.let { s.copy(overview = transform(it), updatedAt = now) } ?: s }
        if (_state.value.connection != ConnectionStatus.ONLINE && _state.value.realtime == RealtimeState.CONNECTED) {
            setConnection(ConnectionStatus.ONLINE)
        }
        persist(immediately = false)
    }

    /** Writes the cache and updates the widget; realtime bursts (energy every 5 s) are coalesced. */
    private fun persist(immediately: Boolean) {
        if (!immediately && persistJob?.isActive == true) return
        persistJob?.cancel()
        persistJob = scope.launch {
            if (!immediately) delay(30_000)
            val s = _state.value
            val o = s.overview ?: return@launch
            OverviewCache.write(context, CachedOverview((s.updatedAt ?: Instant.now()).toString(), o))
            runCatching { HomeWidget().updateAll(context) }
        }
    }

    private fun tick() {
        val timedOut = tracker.expire(Instant.now())
        timedOut.forEach(::reportCommand)
        syncCommands()
    }

    // ------------------------------------------------------------------
    // Failures and connection state.
    // ------------------------------------------------------------------

    /** Shared failure handling for the UI, worker and widget. */
    fun reportFailure(e: Throwable, quiet: Boolean = false) = onFailure(e, quiet)

    private fun onFailure(e: Throwable, quiet: Boolean = false) {
        val kind = Failures.classify(e)
        when (kind) {
            FailureKind.UNPAIRED -> {
                val reason = (e as? ApiException)?.unpairReason ?: UnpairReason.REVOKED
                val rotated = tokenRotatedAt
                val justRotated = rotated != null && Duration.between(rotated, Instant.now()) < Duration.ofMinutes(1)
                // A request that left with the old token right before the refresh: not a revocation.
                if (!(reason == UnpairReason.UNKNOWN_TOKEN && justRotated)) markUnpaired(reason)
            }
            FailureKind.FORBIDDEN, FailureKind.APP_ERROR -> if (!quiet) _messages.tryEmit(e.message ?: "Er ging iets mis.")
            FailureKind.WRONG_SERVER -> setConnection(ConnectionStatus.WRONG_SERVER)
            FailureKind.NO_ROUTE -> {
                if (_state.value.unpaired == null) {
                    setConnection(ConnectionStatus.OFFLINE, (e as? NoRouteException)?.choice?.blockedReason)
                }
            }
            FailureKind.NETWORK, FailureKind.SERVER_DOWN, FailureKind.STARTING -> {
                setConnection(ConnectionStatus.from(kind) ?: ConnectionStatus.OFFLINE)
                realtime.reconnectNow()
            }
        }
    }

    private fun unpairedBy(e: Exception): Boolean {
        if (Failures.classify(e) != FailureKind.UNPAIRED) return false
        onFailure(e)
        return true
    }

    private fun setConnection(status: ConnectionStatus, detail: String? = null) {
        val old = _state.value.connection
        _state.update {
            it.copy(
                connection = status,
                connectionDetail = detail ?: ConnectionText.banner(status),
                online = status == ConnectionStatus.ONLINE,
                route = if (status == ConnectionStatus.ONLINE) routes.route ?: it.route else routes.route,
            )
        }
        ConnectionText.transition(old, status)?.let { _messages.tryEmit(it) }
    }

    private fun markUnpaired(reason: UnpairReason) {
        if (_state.value.unpaired != null) return
        store.markUnpaired(reason)
        realtime.stop()
        routes.forget()
        setpoints.cancelAll()
        brightness.cancelAll()
        loopJob?.cancel()
        _state.update {
            it.copy(
                unpaired = reason,
                online = false,
                route = null,
                connection = ConnectionStatus.UNPAIRED,
                connectionDetail = reason.message,
                drafts = emptyMap(),
                pending = emptySet(),
            )
        }
    }

    private fun reloadPairing() {
        val p = store.pairing()
        _state.update { it.copy(pairing = p ?: it.pairing) }
    }

    // ------------------------------------------------------------------
    // Commands (verbeterlijst 23): tracked by command id until command.updated says it is done.
    // ------------------------------------------------------------------

    private fun canOperate(): Boolean {
        val s = _state.value
        if (s.readOnly) {
            _messages.tryEmit("Deze telefoon kan alleen meekijken.")
            return false
        }
        return s.controlsEnabled
    }

    /** +/− buttons: updates the draft immediately, sends one final value 0,8 s after the last tap. */
    fun nudgeTarget(zone: Zone, delta: Double) {
        if (!canOperate()) return
        if (!zone.adjustable) {
            _messages.tryEmit(notAdjustable(zone))
            return
        }
        val next = Setpoint.step(_state.value.shownTarget(zone), delta, zone.capabilities)
        _state.update { it.copy(drafts = it.drafts + (zone.id to next)) }
        setpoints.submit(zone.id, next)
    }

    /** A dial or slider: same debounce as [nudgeTarget], value rounded to the unit step. */
    fun setTarget(zone: Zone, value: Double) {
        if (!canOperate()) return
        if (!zone.adjustable) {
            _messages.tryEmit(notAdjustable(zone))
            return
        }
        val next = Setpoint.step(value, 0.0, zone.capabilities)
        _state.update { it.copy(drafts = it.drafts + (zone.id to next)) }
        setpoints.submit(zone.id, next)
    }

    /** A mode choice is the only thing that may start a unit that is off (verbeterlijst 13). */
    fun setMode(zone: Zone, mode: String) {
        if (!canOperate()) return
        val draft = if (setpoints.isWaiting(zone.id)) _state.value.drafts[zone.id] else null
        setpoints.cancel(zone.id)
        scope.launch { sendZoneCommand(zone.id, ZoneCommand(mode = mode, targetC = draft?.takeIf { mode != "off" })) }
    }

    fun setFan(zone: Zone, fan: String) {
        if (!canOperate()) return
        if (!zone.adjustable || !zone.capabilities.fan) {
            _messages.tryEmit(notAdjustable(zone))
            return
        }
        scope.launch { sendZoneCommand(zone.id, ZoneCommand(fan = fan)) }
    }

    fun overrideFor(zone: Zone, minutes: Int) {
        if (!canOperate()) return
        if (!zone.adjustable) {
            _messages.tryEmit(notAdjustable(zone))
            return
        }
        val target = _state.value.shownTarget(zone)
        setpoints.cancel(zone.id)
        scope.launch { sendZoneCommand(zone.id, ZoneCommand(targetC = target, overrideMinutes = minutes)) }
    }

    /** "Terug naar schema", also from "uit" (verbeterlijst 11). */
    fun resume(zone: Zone) {
        if (!canOperate()) return
        if (!zone.resumable) return
        scope.launch {
            try {
                val updated = routed { api.resume(it, zone.id) }
                updateOverview { o -> o.copy(zones = o.zones.map { if (it.id == updated.id) updated else it }) }
                tracker.clear(zone.id)
                _state.update { it.copy(drafts = it.drafts - zone.id) }
                syncCommands()
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                reportSendFailure(e)
            }
        }
    }

    private fun notAdjustable(zone: Zone): String = when {
        !zone.unitLinked -> "Er is nog geen airco gekoppeld aan ${zone.name}."
        zone.unitLink == UnitLink.OFFLINE -> "De airco van ${zone.name} is niet bereikbaar."
        else -> "Kies eerst een modus: de airco van ${zone.name} staat uit."
    }

    private suspend fun sendZoneCommand(zoneId: String, command: ZoneCommand) {
        sending += zoneId
        try {
            val accepted = routed { api.command(it, zoneId, command) }
            val id = accepted.commandId
            if (id != null) {
                tracker.accepted(id, zoneId, CommandTarget.ZONE, Instant.now(), requested = command)
            }
        } catch (e: CancellationException) {
            throw e
        } catch (e: Exception) {
            _state.update { it.copy(drafts = it.drafts - zoneId) }
            reportSendFailure(e)
        } finally {
            sending -= zoneId
            syncCommands()
        }
    }

    fun toggleGroup(group: LightGroup) {
        if (!canOperate() || !group.reachable) return
        scope.launch { sendLightCommand(group.id, GroupCommand(on = !group.on)) }
    }

    /** Brightness 1–100, sent 0,5 s after the last change. */
    fun setGroupBrightness(group: LightGroup, percent: Int) {
        if (!canOperate() || !group.reachable) return
        brightness.submit(group.id, percent.coerceIn(1, 100))
    }

    fun recallScene(group: LightGroup, sceneId: String) {
        if (!canOperate()) return
        scope.launch { sendLightCommand(group.id, GroupCommand(sceneId = sceneId)) }
    }

    private suspend fun sendLightCommand(groupId: String, command: GroupCommand) {
        try {
            val accepted = routed { api.setGroup(it, groupId, command) }
            accepted.commandId?.let { tracker.accepted(it, groupId, CommandTarget.LIGHT_GROUP, Instant.now(), requestedLight = command) }
        } catch (e: CancellationException) {
            throw e
        } catch (e: Exception) {
            reportSendFailure(e)
        } finally {
            syncCommands()
        }
    }

    fun acknowledge(alert: Alert) {
        if (!canOperate()) return
        scope.launch {
            try {
                routed { api.ack(it, alert.id) }
                updateOverview { o -> o.copy(alerts = o.alerts.filterNot { it.id == alert.id }) }
                AlertNotifier.cancel(context, alert.id)
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                reportSendFailure(e)
            }
        }
    }

    private fun reportSendFailure(e: Exception) {
        val kind = Failures.classify(e)
        if (kind.offline) _messages.tryEmit("Niet verzonden: geen verbinding met je huis.")
        onFailure(e)
    }

    /** A failed, refused or unconfirmed command is reported once, with its reason. */
    private fun reportCommand(c: TrackedCommand) {
        if (!c.status.failed) return
        val o = _state.value.overview
        val name = o?.zones?.firstOrNull { it.id == c.targetId }?.name ?: o?.lights?.firstOrNull { it.id == c.targetId }?.name
        _messages.tryEmit(listOfNotNull(name, c.label).joinToString(": "))
    }

    private fun syncCommands() {
        val snap = tracker.snapshot()
        val inFlight = tracker.inFlightTargets()
        _state.update { s ->
            s.copy(
                commands = snap,
                pending = inFlight,
                // Keep a draft while it is debounced, being sent or in flight; drop it when final.
                drafts = s.drafts.filterKeys { k -> setpoints.isWaiting(k) || k in sending || k in inFlight },
            )
        }
    }

    // ------------------------------------------------------------------
    // Pairing.
    // ------------------------------------------------------------------

    /** Pairs from a parsed QR code or manual entry. Returns a Dutch error, or null on success. */
    suspend fun pair(parsed: PairParse.Ok): String? = pair(parsed.lanUrl, parsed.remoteUrl, parsed.code, parsed.serverId, parsed.role)

    /**
     * Pairs with a code. When the QR code named a [serverId], every address is identity-checked
     * before the code is sent. Addresses must obey the home-network / https rules.
     * Returns a Dutch error message, or null on success.
     */
    suspend fun pair(lanUrl: String?, remoteUrl: String?, code: String, serverId: String? = null, role: String? = null): String? {
        var lan = lanUrl?.let { UrlTools.normalizeBaseUrl(it) ?: return "Dit adres klopt niet." }
        var remote = remoteUrl?.let { UrlTools.normalizeBaseUrl(it) ?: return "Dit adres klopt niet." }
        // An https address on the home network is a home address, whatever field it came in.
        if (lan == null && remote != null && UrlTools.host(remote)?.let(HostRules::isPrivateHost) == true) {
            lan = remote
            remote = null
        }
        lan?.let { ServerUrls.lanProblem(it) }?.let { return it }
        remote?.let { ServerUrls.remoteProblem(it) }?.let { return it }
        if (lan == null && remote == null) return "Vul het adres van je server in."
        val expected = serverId?.let { ServerIdentityCheck.normalize(it) ?: return "Dit is geen koppelcode van je Huis-app." }

        val deviceName = listOfNotNull(Build.MANUFACTURER, Build.MODEL).joinToString(" ").ifBlank { "Android" }
        val candidates = listOfNotNull(lan?.let { it to RouteKind.LAN }, remote?.let { it to RouteKind.REMOTE })
        var lastError = "De server is niet bereikbaar op dit adres."
        var wrongServer = false
        for ((base, kind) in candidates) {
            if (expected != null) {
                when (val p = probe(base, kind)) {
                    Probe.Unreachable -> continue
                    is Probe.Blocked -> {
                        lastError = p.reason
                        continue
                    }
                    is Probe.Answered -> if (ServerIdentityCheck.verify(expected, p.serverId) != IdentityVerdict.MATCH) {
                        wrongServer = true
                        continue
                    }
                }
            }
            try {
                val r = api.redeem(base, kind, code, deviceName)
                val sid = ServerIdentityCheck.normalize(r.server.serverId)
                    ?: return "Je server is te oud voor deze app. Werk de server bij en probeer het opnieuw."
                if (expected != null && sid != expected) {
                    return "Dit is niet jouw server: het adres hoort bij een andere installatie."
                }
                val serverLan = r.server.lanUrl?.let { UrlTools.normalizeBaseUrl(it) }?.takeIf { ServerUrls.lanProblem(it) == null }
                val serverRemote = r.server.remoteUrl?.let { UrlTools.normalizeBaseUrl(it) }?.takeIf { ServerUrls.remoteProblem(it) == null }
                val pairing = Pairing(
                    token = r.token,
                    lanUrl = serverLan ?: lan,
                    remoteUrl = serverRemote ?: remote,
                    homeId = r.server.homeId,
                    homeName = r.server.homeName,
                    username = r.user.username,
                    role = r.user.role,
                    serverId = sid,
                    clientRole = r.client?.role ?: role,
                    tokenExpiresAt = r.client?.expiresAt,
                )
                if (!store.savePairing(pairing)) {
                    return "De koppeling kon niet veilig op deze telefoon worden opgeslagen. Probeer het opnieuw."
                }
                val route = Route(kind, base, sid)
                routes.adopt(route)
                stop()
                _state.value = HomeState(
                    pairing = store.pairing() ?: pairing,
                    route = route,
                    online = true,
                    connection = ConnectionStatus.ONLINE,
                    notificationsWaiting = _state.value.notificationsWaiting,
                )
                AlertWorker.schedule(context)
                start(forgetRoute = false)
                return null
            } catch (e: CancellationException) {
                throw e
            } catch (e: ApiException) {
                // A real answer from the server (e.g. expired code): don't try the other address.
                if (e.fromApi) return e.message
                lastError = e.message
            } catch (e: Exception) {
                lastError = "De server is niet bereikbaar op dit adres."
            }
        }
        return if (wrongServer) {
            "Dit is niet jouw server: op dit adres antwoordt een ander apparaat. De app stuurt de koppelcode er niet heen."
        } else {
            lastError
        }
    }

    /**
     * "Ontkoppelen": revokes the token on the server (DELETE /clients/self) and then forgets
     * everything on this phone. The local data is wiped even when the server cannot be reached.
     */
    suspend fun unpairAndForget(): UnpairResult {
        var result = UnpairResult.LOCAL_ONLY
        val s = _state.value
        if (s.pairing != null) {
            if (s.unpaired != null) {
                result = UnpairResult.REVOKED_ON_SERVER // the token is already dead
            } else {
                try {
                    withTimeout(12_000) { routed { api.unpairSelf(it) } }
                    result = UnpairResult.REVOKED_ON_SERVER
                } catch (e: TimeoutCancellationException) {
                    // fall through: local only
                } catch (e: CancellationException) {
                    throw e
                } catch (e: Exception) {
                    if (Failures.classify(e) == FailureKind.UNPAIRED) result = UnpairResult.REVOKED_ON_SERVER
                }
            }
        }
        forgetPairing()
        return result
    }

    /** Kept for older UI code: same as [unpairAndForget], without waiting for the result. */
    fun unpair() {
        scope.launch { unpairAndForget() }
    }

    /** Forgets the pairing on this phone only (after "niet meer gekoppeld", before pairing again). */
    fun forgetPairing() {
        stop()
        routes.forget()
        setpoints.cancelAll()
        brightness.cancelAll()
        store.clear()
        OverviewCache.clear(context)
        AlertWorker.cancel(context)
        AlertNotifier.cancelAll(context)
        tracker.snapshot().keys.forEach(tracker::clear)
        _state.value = HomeState(connection = ConnectionStatus.CONNECTING)
        scope.launch { runCatching { HomeWidget().updateAll(context) } }
    }

    fun saveCfAccess(id: String?, secret: String?) {
        store.saveCfAccess(id, secret)
        retry()
    }

    /**
     * "Adressen bewerken" on the phone. Values are checked with the same rules as a QR code; the
     * token only goes to an address that answers as the paired server. Returns a Dutch error or null.
     */
    fun updateAddresses(lanUrl: String?, remoteUrl: String?): String? {
        val lan = lanUrl?.takeIf { it.isNotBlank() }?.let { UrlTools.normalizeBaseUrl(it) ?: return "Het thuisadres klopt niet." }
        val remote = remoteUrl?.takeIf { it.isNotBlank() }?.let { UrlTools.normalizeBaseUrl(it) ?: return "Het externe adres klopt niet." }
        lan?.let { ServerUrls.lanProblem(it) }?.let { return it }
        remote?.let { ServerUrls.remoteProblem(it) }?.let { return it }
        if (lan == null && remote == null) return "Vul minstens één adres in."
        if (_state.value.pairing == null) return "Deze telefoon is niet gekoppeld."
        store.updateAddresses(lan, remote)
        reloadPairing()
        retry()
        return null
    }

    /** Set by the background worker: alerts waiting for notification permission. */
    fun setNotificationsWaiting(count: Int) {
        _state.update { it.copy(notificationsWaiting = count) }
    }
}
