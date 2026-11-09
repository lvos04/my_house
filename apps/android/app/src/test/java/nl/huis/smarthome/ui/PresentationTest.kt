package nl.huis.smarthome.ui

import androidx.camera.core.CameraState
import nl.huis.smarthome.core.ApiProblem
import nl.huis.smarthome.core.CommandLabel
import nl.huis.smarthome.core.CommandStatus
import nl.huis.smarthome.core.CommandTarget
import nl.huis.smarthome.core.ConnectionStatus
import nl.huis.smarthome.core.LightGroup
import nl.huis.smarthome.core.Measurement
import nl.huis.smarthome.core.Override
import nl.huis.smarthome.core.PendingCommandRef
import nl.huis.smarthome.core.Quality
import nl.huis.smarthome.core.StatusItem
import nl.huis.smarthome.core.TrackedCommand
import nl.huis.smarthome.core.Zone
import nl.huis.smarthome.core.ZoneCapabilities
import nl.huis.smarthome.core.ZoneCommand
import nl.huis.smarthome.core.ZonePolicy
import nl.huis.smarthome.data.HomeState
import nl.huis.smarthome.data.Pairing
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.time.Instant

class PresentationTest {
    private val pairing = Pairing("shc_x", "http://192.168.1.20:8088", null, "h1", "Thuis", "telefoon", "admin", clientRole = "control")
    private val online = HomeState(pairing = pairing, online = true, connection = ConnectionStatus.ONLINE)
    private val viewer = online.copy(pairing = pairing.copy(clientRole = "viewer"))

    private fun zone(
        status: String = "online",
        mode: String = "heat",
        control: String = "schedule",
        adjustable: Boolean? = true,
        canResume: Boolean? = false,
        fan: Boolean = true,
    ) = Zone(
        id = "z1",
        name = "Woonkamer",
        online = status == "online",
        mode = mode,
        targetC = 21.0,
        control = control,
        unitLinked = status != "not_linked",
        unitStatus = status,
        canResume = canResume,
        setpointAdjustable = adjustable,
        capabilities = ZoneCapabilities(modes = listOf("off", "heat", "cool", "auto"), fan = fan),
    )

    // ---- verbeterlijst 13, 31, 11 ----

    @Test
    fun runningUnitHasEveryControl() {
        val c = ZoneUi.controls(zone(), online)
        assertEquals(UnitState.ON, ZoneUi.unitState(zone()))
        assertTrue(c.visible && c.setpointEnabled && c.fanEnabled && c.modesEnabled && c.overrideEnabled)
        assertNull(c.hint)
        assertFalse(c.showResume)
    }

    @Test
    fun unitOffDisablesSetpointFanAndOverrideButNotModes() {
        val z = zone(mode = "off", control = "off", adjustable = false, canResume = true)
        val c = ZoneUi.controls(z, online)
        assertEquals(UnitState.OFF, ZoneUi.unitState(z))
        assertTrue(c.visible)
        assertFalse(c.setpointEnabled)
        assertFalse(c.fanEnabled)
        assertFalse(c.overrideEnabled)
        // Only a mode choice can start the unit.
        assertTrue(c.modesEnabled)
        assertEquals(ZoneHint.CHOOSE_MODE, c.hint)
        // "Terug naar schema" also when off (can_resume).
        assertTrue(c.showResume)
        assertTrue(c.resumeEnabled)
    }

    @Test
    fun offlineUnitIsAWarningAndOnlyResumeWorks() {
        val z = zone(status = "offline", adjustable = false, control = "manual", canResume = true)
        val c = ZoneUi.controls(z, online)
        assertEquals(UnitState.OFFLINE, ZoneUi.unitState(z))
        assertEquals(ZoneHint.UNIT_OFFLINE, c.hint)
        assertFalse(c.modesEnabled)
        assertFalse(c.setpointEnabled)
        assertTrue(c.showResume)
        assertEquals(ZonePill.UNREACHABLE, ZoneUi.pill(z))
    }

    @Test
    fun zoneWithoutAircoIsNeutralWithoutControls() {
        val z = zone(status = "not_linked", adjustable = false).copy(temperature = Measurement(20.5, "°C", Quality.MEASURED))
        val c = ZoneUi.controls(z, online)
        assertEquals(UnitState.NOT_LINKED, ZoneUi.unitState(z))
        assertFalse(c.visible)
        assertNull(c.hint)
        assertEquals(ZonePill.NO_UNIT, ZoneUi.pill(z))
        assertTrue(ZoneUi.hasSensor(z))
        assertFalse(ZoneUi.hasSensor(zone(status = "not_linked")))
    }

    @Test
    fun olderServerFieldsFallBackToZoneState() {
        // No setpoint_adjustable / unit_status: derived from online + mode.
        val off = zone(status = "online", mode = "off", adjustable = null).copy(unitStatus = null)
        assertEquals(UnitState.OFF, ZoneUi.unitState(off))
        val unlinked = zone(adjustable = null).copy(unitStatus = null, unitLinked = false)
        assertEquals(UnitState.NOT_LINKED, ZoneUi.unitState(unlinked))
    }

    @Test
    fun viewerPhoneSeesNoControls() {
        val c = ZoneUi.controls(zone(), viewer)
        assertFalse(c.visible)
        assertFalse(c.setpointEnabled || c.modesEnabled || c.resumeEnabled)
        assertNull(c.hint)
    }

    @Test
    fun offlineAppDisablesEverythingButKeepsTheLayout() {
        val c = ZoneUi.controls(zone(), online.copy(online = false, connection = ConnectionStatus.OFFLINE))
        assertTrue(c.visible)
        assertFalse(c.setpointEnabled || c.modesEnabled || c.overrideEnabled || c.resumeEnabled)
    }

    @Test
    fun fanNeedsTheCapability() {
        assertFalse(ZoneUi.controls(zone(fan = false), online).fanEnabled)
    }

    @Test
    fun pillShowsManualWithOrWithoutEndTime() {
        assertEquals(ZonePill.MANUAL, ZoneUi.pill(zone(control = "manual")))
        assertEquals(ZonePill.MANUAL, ZoneUi.pill(zone().copy(override = Override(until = "2026-11-08T15:00:00Z"))))
        assertEquals(ZonePill.MODE, ZoneUi.pill(zone()))
    }

    @Test
    fun modesStartWithOffAndFallBackForOldServers() {
        assertEquals(listOf("off", "heat", "cool", "auto"), ZoneUi.modes(zone()))
        val none = zone().copy(capabilities = ZoneCapabilities(modes = emptyList()))
        assertEquals(listOf("off", "heat", "cool"), ZoneUi.modes(none))
    }

    @Test
    fun overrideDurationFollowsPolicy() {
        assertEquals(120, ZoneUi.overrideMinutes(zone()))
        assertEquals(90, ZoneUi.overrideMinutes(zone().copy(policy = ZonePolicy(overrideDefaultMinutes = 90))))
        assertEquals(120, ZoneUi.overrideMinutes(zone().copy(policy = ZonePolicy(overrideDefaultMinutes = 0))))
        assertEquals(HoursMinutes(2, 0), HoursMinutes.of(120))
        assertEquals(HoursMinutes(0, 45), HoursMinutes.of(45))
        assertEquals(HoursMinutes(1, 30), HoursMinutes.of(90))
    }

    // ---- verbeterlijst 23 ----

    private val t0 = Instant.parse("2026-11-08T13:40:00Z")

    private fun tracked(status: CommandStatus, target: Double? = 22.0, detail: String? = null) =
        TrackedCommand("c1", "z1", CommandTarget.ZONE, status, detail, requested = ZoneCommand(targetC = target), sentAt = t0, updatedAt = t0)

    @Test
    fun draftWithoutAcceptedCommandIsSending() {
        val s = online.copy(drafts = mapOf("z1" to 22.0))
        assertEquals(CommandLine.Sending, CommandUi.line(s, zone()))
    }

    @Test
    fun newDraftWhileOlderCommandIsInFlightIsSending() {
        val s = online.copy(drafts = mapOf("z1" to 22.5), commands = mapOf("z1" to tracked(CommandStatus.SENT, 22.0)))
        assertEquals(CommandLine.Sending, CommandUi.line(s, zone()))
    }

    @Test
    fun trackedCommandShowsServerOutcomeWithTone() {
        val inFlight = online.copy(drafts = mapOf("z1" to 22.0), commands = mapOf("z1" to tracked(CommandStatus.QUEUED)))
        val l1 = CommandUi.line(inFlight, zone()) as CommandLine.Tracked
        assertEquals(Tone.MUTED, l1.tone)
        // A code: the composable shows the strings.xml text ("Verzonden · wacht op bevestiging…").
        assertEquals(CommandLabel.QUEUED, l1.label)
        assertNull(l1.reason)

        val ok = online.copy(commands = mapOf("z1" to tracked(CommandStatus.CONFIRMED)))
        assertEquals(Tone.OK, (CommandUi.line(ok, zone()) as CommandLine.Tracked).tone)

        val denied = online.copy(commands = mapOf("z1" to tracked(CommandStatus.DENIED, detail = "buitenunit koelt al")))
        val l3 = CommandUi.line(denied, zone()) as CommandLine.Tracked
        assertEquals(Tone.WARN, l3.tone)
        assertEquals(CommandLine.Tracked(CommandLabel.DENIED, "buitenunit koelt al", Tone.WARN), l3)

        assertEquals(Tone.WARN, CommandUi.tone(CommandStatus.TIMEOUT))
        assertEquals(Tone.MUTED, CommandUi.tone(CommandStatus.RETRYING))
    }

    @Test
    fun commandFromElsewhereIsShownUntilFinal() {
        val z = zone().copy(pendingCommand = PendingCommandRef("c9", "sent"))
        assertEquals(CommandLine.Elsewhere(sent = true), CommandUi.line(online, z))
        assertEquals(CommandLine.Elsewhere(sent = false), CommandUi.line(online, z.copy(pendingCommand = PendingCommandRef("c9", "queued"))))
        assertNull(CommandUi.line(online, z.copy(pendingCommand = PendingCommandRef("c9", "confirmed"))))
        assertNull(CommandUi.line(online, zone()))
    }

    // ---- verbeterlijst 17, 37: banner ----

    @Test
    fun bannerFollowsConnectionStatus() {
        assertEquals(BannerKind.NONE, ConnectionUi.banner(online).kind)
        val offline = online.copy(online = false, connection = ConnectionStatus.OFFLINE)
        assertEquals(Banner(BannerKind.OFFLINE, null), ConnectionUi.banner(offline))
        // Cloudflare Access refused the tunnel: a code, shown with its strings.xml text.
        val cf = offline.copy(connectionProblem = ApiProblem.CF_ACCESS_LOGIN)
        assertEquals(Banner(BannerKind.OFFLINE, ApiProblem.CF_ACCESS_LOGIN), ConnectionUi.banner(cf))
        // Only an offline banner carries it.
        assertEquals(Banner(BannerKind.STARTING), ConnectionUi.banner(cf.copy(connection = ConnectionStatus.STARTING)))
        assertEquals(BannerKind.STARTING, ConnectionUi.banner(offline.copy(connection = ConnectionStatus.STARTING)).kind)
        assertEquals(BannerKind.WRONG_SERVER, ConnectionUi.banner(offline.copy(connection = ConnectionStatus.WRONG_SERVER)).kind)
        assertEquals(BannerKind.CONNECTING, ConnectionUi.banner(HomeState(pairing = pairing)).kind)
        // The unpaired screen replaces everything; no banner.
        assertEquals(BannerKind.NONE, ConnectionUi.banner(offline.copy(connection = ConnectionStatus.UNPAIRED)).kind)
        // Inconsistent flags count as offline.
        assertEquals(BannerKind.OFFLINE, ConnectionUi.banner(online.copy(online = false)).kind)
    }

    // ---- QR scanner ----

    @Test
    fun qrGateThrottlesRepeatsAndRemembersRejectedCodes() {
        val gate = QrGate(repeatMs = 3_000)
        assertTrue(gate.offer("A", 1_000))
        assertFalse(gate.offer("A", 2_000))
        assertTrue(gate.offer("B", 2_100))
        assertTrue(gate.offer("A", 5_000))
        gate.reject("A")
        // A rejected code is never resubmitted on its own, however long it stays in view.
        assertFalse(gate.offer("A", 60_000))
        assertTrue(gate.isRejected("A"))
        assertTrue(gate.offer("C", 60_100))
        gate.reset()
        assertTrue(gate.offer("A", 60_200))
    }

    // ---- notifications and camera permission ----

    @Test
    fun notificationExplanationComesFirstAndOnlyOnce() {
        assertEquals(NotifPrompt.EXPLAIN, NotificationPolicy.prompt(canPost = false, runtimePermission = true, explained = false, waiting = 0))
        assertEquals(NotifPrompt.NONE, NotificationPolicy.prompt(canPost = false, runtimePermission = true, explained = true, waiting = 0))
        assertEquals(NotifPrompt.WAITING, NotificationPolicy.prompt(canPost = false, runtimePermission = true, explained = true, waiting = 2))
        assertEquals(NotifPrompt.WAITING, NotificationPolicy.prompt(canPost = false, runtimePermission = false, explained = false, waiting = 1))
        assertEquals(NotifPrompt.NONE, NotificationPolicy.prompt(canPost = true, runtimePermission = true, explained = false, waiting = 3))
    }

    @Test
    fun afterDontAskAgainTheButtonOpensSettings() {
        assertEquals(NotifAction.REQUEST, NotificationPolicy.action(runtimePermission = true, granted = false, askedSystem = false, showRationale = false))
        assertEquals(NotifAction.REQUEST, NotificationPolicy.action(runtimePermission = true, granted = false, askedSystem = true, showRationale = true))
        assertEquals(NotifAction.OPEN_SETTINGS, NotificationPolicy.action(runtimePermission = true, granted = false, askedSystem = true, showRationale = false))
        // Granted but switched off in the settings, or Android 12 and older.
        assertEquals(NotifAction.OPEN_SETTINGS, NotificationPolicy.action(runtimePermission = true, granted = true, askedSystem = true, showRationale = false))
        assertEquals(NotifAction.OPEN_SETTINGS, NotificationPolicy.action(runtimePermission = false, granted = true, askedSystem = false, showRationale = false))

        assertFalse(CameraPolicy.openSettings(granted = false, askedBefore = false, showRationale = false))
        assertFalse(CameraPolicy.openSettings(granted = false, askedBefore = true, showRationale = true))
        assertTrue(CameraPolicy.openSettings(granted = false, askedBefore = true, showRationale = false))
        assertFalse(CameraPolicy.openSettings(granted = true, askedBefore = true, showRationale = false))
    }

    // ---- small helpers ----

    @Test
    fun spokenTemperatureUsesDutchDecimals() {
        assertEquals("21,5", Spoken.temperature(21.5))
        assertNull(Spoken.temperature(null))
    }

    @Test
    fun hiddenLightGroupsAreLeftOutAndAllLightsComesLast() {
        val groups = listOf(
            LightGroup("all", "Alle lampen", allLights = true),
            LightGroup("k", "Keuken"),
            LightGroup("x", "Verborgen", hidden = true),
            LightGroup("w", "Woonkamer"),
        )
        assertEquals(listOf("k", "w", "all"), LightsUi.visible(groups).map { it.id })
    }

    @Test
    fun attentionBlockShowsWorkerSafeModeBackupAndMaintenance() {
        val worker = StatusItem("worker", "Achtergronddienst", "error", "Geen hartslag")
        val safe = StatusItem("safe_mode", "Veilige modus", "warn", "Geen apparaatcommando's")
        val backupOk = StatusItem("backup", "Back-up", "ok", "Laatste back-up 3 uur geleden")
        val backupOld = StatusItem("backup", "Back-up", "warn", "Laatste back-up 9 dagen geleden")
        val integration = StatusItem("integration:1:mqtt", "Zigbee", "error", "Niet verbonden")
        val unknown = StatusItem("maintenance", "Onderhoud", "unknown")

        // Same selection as attentionItems() on the web overview; errors first.
        val a = AttentionUi.of(listOf(safe, worker, backupOk, integration, unknown))
        assertEquals(listOf("worker", "safe_mode"), a.items.map { it.key })
        assertEquals(AttentionLevel.ERROR, a.level)

        val warn = AttentionUi.of(listOf(backupOld, StatusItem("worker", "Achtergronddienst", "ok")))
        assertEquals(listOf("backup"), warn.items.map { it.key })
        assertEquals(AttentionLevel.WARN, warn.level)

        assertEquals(AttentionLevel.NONE, AttentionUi.of(listOf(backupOk, integration)).level)
        assertEquals(AttentionLevel.NONE, AttentionUi.of(emptyList()).level)
    }

    @Test
    fun cameraErrorOffersRetryOnlyWithACamera() {
        assertTrue(CameraPolicy.canRetry(CameraIssue.IN_USE, hasHardware = true))
        assertTrue(CameraPolicy.canRetry(CameraIssue.DISABLED, hasHardware = true))
        assertFalse(CameraPolicy.canRetry(CameraIssue.UNAVAILABLE, hasHardware = false))
        assertFalse(CameraPolicy.canRetry(null, hasHardware = true))
    }

    @Test
    fun cameraStateErrorsBecomeAMessageNotABlackPicture() {
        assertEquals(CameraIssue.IN_USE, CameraPolicy.issueFor(CameraState.ERROR_CAMERA_IN_USE))
        assertEquals(CameraIssue.IN_USE, CameraPolicy.issueFor(CameraState.ERROR_MAX_CAMERAS_IN_USE))
        assertEquals(CameraIssue.DISABLED, CameraPolicy.issueFor(CameraState.ERROR_CAMERA_DISABLED))
        assertEquals(CameraIssue.UNAVAILABLE, CameraPolicy.issueFor(CameraState.ERROR_CAMERA_FATAL_ERROR))
        assertEquals(CameraIssue.UNAVAILABLE, CameraPolicy.issueFor(CameraState.ERROR_STREAM_CONFIG))
        assertEquals(CameraIssue.UNAVAILABLE, CameraPolicy.issueFor(CameraState.ERROR_DO_NOT_DISTURB_MODE_ENABLED))
        assertEquals(CameraIssue.UNAVAILABLE, CameraPolicy.issueFor(CameraState.ERROR_OTHER_RECOVERABLE_ERROR))
    }
}
