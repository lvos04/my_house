package nl.huis.smarthome.ui

import androidx.camera.core.CameraState
import nl.huis.smarthome.R
import nl.huis.smarthome.core.ApiProblem
import nl.huis.smarthome.core.CommandLabel
import nl.huis.smarthome.core.CommandStatus
import nl.huis.smarthome.core.ConnectionStatus
import nl.huis.smarthome.core.LightGroup
import nl.huis.smarthome.core.NlFormat
import nl.huis.smarthome.core.PendingCommandRef
import nl.huis.smarthome.core.RelativeTime
import nl.huis.smarthome.core.StatusItem
import nl.huis.smarthome.core.UnitLink
import nl.huis.smarthome.core.Zone
import nl.huis.smarthome.data.CoreTexts
import nl.huis.smarthome.data.HomeState
import nl.huis.smarthome.data.TextSource
import java.time.Duration
import kotlin.math.roundToLong

// ---------------------------------------------------------------------------------------------
// What the screens show, decided without Android so it can be unit-tested (app/src/test/.../ui).
// The composables only map these values to strings and colours.
// ---------------------------------------------------------------------------------------------

/** The airco of a zone, as far as the controls are concerned (verbeterlijst 13, 31). */
enum class UnitState {
    /** No airco linked yet: neutral text, never red, no controls. */
    NOT_LINKED,

    /** Linked but not answering: warning, only "Terug naar schema" stays possible. */
    OFFLINE,

    /** Linked and reachable, but (effectively) off: only a mode choice can start it. */
    OFF,

    /** Running: +/−, fan and "Handmatig" work. */
    ON,
}

/** Why +/−, fan and "Handmatig" are disabled while the controls are otherwise shown. */
enum class ZoneHint { CHOOSE_MODE, UNIT_OFFLINE }

/** The small label at the top right of a zone card. */
enum class ZonePill { NO_UNIT, UNREACHABLE, MANUAL, MODE }

data class ZoneControls(
    /** Show the controls at all: false for a viewer phone and for a zone without airco. */
    val visible: Boolean,
    val setpointEnabled: Boolean,
    val fanEnabled: Boolean,
    val modesEnabled: Boolean,
    val overrideEnabled: Boolean,
    val resumeEnabled: Boolean,
    /** "Terug naar schema" instead of "Handmatig …"; also when the unit is off (verbeterlijst 11). */
    val showResume: Boolean,
    val hint: ZoneHint?,
)

object ZoneUi {
    const val DEFAULT_OVERRIDE_MINUTES = 120

    val FAN_SPEEDS = listOf("auto", "low", "medium", "high")

    fun unitState(zone: Zone): UnitState = when (zone.unitLink) {
        UnitLink.NOT_LINKED -> UnitState.NOT_LINKED
        UnitLink.OFFLINE -> UnitState.OFFLINE
        UnitLink.ONLINE -> if (zone.adjustable) UnitState.ON else UnitState.OFF
    }

    fun controls(zone: Zone, state: HomeState): ZoneControls {
        val unit = unitState(zone)
        val visible = !state.readOnly && unit != UnitState.NOT_LINKED
        // Online, still paired and allowed to operate (false for viewer phones).
        val live = visible && state.controlsEnabled
        val running = unit == UnitState.ON
        return ZoneControls(
            visible = visible,
            setpointEnabled = live && running,
            fanEnabled = live && running && zone.capabilities.fan,
            modesEnabled = live && unit != UnitState.OFFLINE,
            overrideEnabled = live && running,
            resumeEnabled = live,
            showResume = zone.resumable,
            hint = when {
                !visible -> null
                unit == UnitState.OFFLINE -> ZoneHint.UNIT_OFFLINE
                unit == UnitState.OFF -> ZoneHint.CHOOSE_MODE
                else -> null
            },
        )
    }

    fun pill(zone: Zone): ZonePill = when {
        zone.unitLink == UnitLink.NOT_LINKED -> ZonePill.NO_UNIT
        zone.unitLink == UnitLink.OFFLINE -> ZonePill.UNREACHABLE
        zone.override != null || zone.control == "manual" -> ZonePill.MANUAL
        else -> ZonePill.MODE
    }

    /** "Uit" first, then the modes the unit supports (heat/cool for servers that send none). */
    fun modes(zone: Zone): List<String> {
        val supported = zone.capabilities.modes.filter { it != "off" }.ifEmpty { listOf("heat", "cool") }
        return (listOf("off") + supported).distinct()
    }

    /** Duration of "Handmatig voor …": the zone's policy, else 2 hours. */
    fun overrideMinutes(zone: Zone): Int =
        zone.policy.overrideDefaultMinutes?.takeIf { it in 1..24 * 60 } ?: DEFAULT_OVERRIDE_MINUTES

    /** Whether the zone has a sensor reading worth showing although no airco is linked. */
    fun hasSensor(zone: Zone): Boolean = zone.sensorDeviceId != null || zone.temperature.value != null
}

/** Hours and minutes for "Handmatig voor 2 uur" / "90 minuten" / "1 uur 30 min". */
data class HoursMinutes(val hours: Int, val minutes: Int) {
    companion object {
        fun of(totalMinutes: Int): HoursMinutes {
            val m = totalMinutes.coerceAtLeast(0)
            return if (m < 60) HoursMinutes(0, m) else HoursMinutes(m / 60, m % 60)
        }
    }
}

// ---------------------------------------------------------------------------------------------
// Command status line (verbeterlijst 23).
// ---------------------------------------------------------------------------------------------

enum class Tone { MUTED, OK, WARN }

sealed interface CommandLine {
    /** The value is still being collected (+/− debounce) or on its way to the server. */
    data object Sending : CommandLine

    /**
     * A command from this phone, with the server's outcome: [label] is shown with its strings.xml
     * text, followed by the server's [reason] after "Mislukt" or "Geweigerd".
     */
    data class Tracked(val label: CommandLabel, val reason: String?, val tone: Tone) : CommandLine

    /** A command from another phone, the web app or the schedule that is not finished yet. */
    data class Elsewhere(val sent: Boolean) : CommandLine
}

object CommandUi {
    fun tone(status: CommandStatus): Tone = when {
        status == CommandStatus.CONFIRMED -> Tone.OK
        status.failed -> Tone.WARN
        else -> Tone.MUTED
    }

    /**
     * What to say under a zone's or light group's controls. [pending] is the zone's
     * `pending_command` from the server, used when this phone has no command of its own.
     */
    fun line(state: HomeState, targetId: String, pending: PendingCommandRef? = null): CommandLine? {
        val cmd = state.command(targetId)
        val draft = state.drafts[targetId]
        if (draft != null && (cmd == null || !cmd.inFlight || cmd.requested?.targetC != draft)) return CommandLine.Sending
        if (cmd != null) return CommandLine.Tracked(cmd.labelCode, cmd.reason, tone(cmd.status))
        val status = pending?.let { CommandStatus.fromWire(it.status) } ?: return null
        if (status.final) return null
        return CommandLine.Elsewhere(sent = status == CommandStatus.SENT)
    }

    fun line(state: HomeState, zone: Zone): CommandLine? = line(state, zone.id, zone.pendingCommand)

    fun line(state: HomeState, group: LightGroup): CommandLine? = line(state, group.id, null)
}

// ---------------------------------------------------------------------------------------------
// Connection banner (verbeterlijst 17, 37): offline, starting, wrong server.
// ---------------------------------------------------------------------------------------------

enum class BannerKind { NONE, CONNECTING, OFFLINE, STARTING, WRONG_SERVER }

data class Banner(
    val kind: BannerKind,
    /** A specific reason (Cloudflare Access) instead of the standard offline text; shown from strings.xml. */
    val problem: ApiProblem? = null,
)

object ConnectionUi {
    fun banner(state: HomeState): Banner {
        val status = if (!state.online && state.connection == ConnectionStatus.ONLINE) ConnectionStatus.OFFLINE else state.connection
        return when (status) {
            ConnectionStatus.ONLINE, ConnectionStatus.UNPAIRED -> Banner(BannerKind.NONE)
            ConnectionStatus.CONNECTING -> Banner(BannerKind.CONNECTING)
            ConnectionStatus.STARTING -> Banner(BannerKind.STARTING)
            ConnectionStatus.WRONG_SERVER -> Banner(BannerKind.WRONG_SERVER)
            ConnectionStatus.OFFLINE -> Banner(BannerKind.OFFLINE, state.connectionProblem)
        }
    }
}

// ---------------------------------------------------------------------------------------------
// Accessibility helpers.
// ---------------------------------------------------------------------------------------------

object Spoken {
    /** "21,5" for TalkBack ("graden" comes from strings.xml); null when unknown. */
    fun temperature(c: Double?): String? = c?.let { NlFormat.temperature(it).removeSuffix("°") }
}

// ---------------------------------------------------------------------------------------------
// QR scanner: the same code arrives many times per second (verbeterlijst, kleine punten).
// ---------------------------------------------------------------------------------------------

/**
 * Decides whether a scanned QR value is sent on. The same value is passed at most once per
 * [repeatMs], and a value that was rejected (invalid code, wrong server, expired code …) is not
 * sent again until [reset] (the user taps "Opnieuw scannen" or switches to manual entry).
 */
class QrGate(private val repeatMs: Long = 3_000) {
    private var lastValue: String? = null
    private var lastAt: Long = 0
    private var rejected: String? = null

    @Synchronized
    fun offer(value: String, now: Long): Boolean {
        if (value == rejected) return false
        if (value == lastValue && now - lastAt < repeatMs) return false
        lastValue = value
        lastAt = now
        return true
    }

    @Synchronized
    fun reject(value: String) {
        rejected = value
    }

    @Synchronized
    fun reset() {
        rejected = null
        lastValue = null
        lastAt = 0
    }

    @Synchronized
    fun isRejected(value: String): Boolean = value == rejected
}

// ---------------------------------------------------------------------------------------------
// Phone notifications (verbeterlijst, kleine punten "Android-meldingen").
// ---------------------------------------------------------------------------------------------

enum class NotifPrompt {
    NONE,

    /** First time: explain why, with "Meldingen toestaan" and "Niet nu". Never asked automatically. */
    EXPLAIN,

    /** Notifications are off while alerts are waiting: offer the settings. */
    WAITING,
}

enum class NotifAction {
    /** Show the system permission question (Android 13+). */
    REQUEST,

    /** Open the app's notification settings (after "niet meer vragen", or switched off there). */
    OPEN_SETTINGS,
}

object NotificationPolicy {
    /**
     * @param canPost notifications can be shown now
     * @param runtimePermission Android 13+: the app has to ask for POST_NOTIFICATIONS
     * @param explained the explanation was answered once on this install ("Toestaan" or "Niet nu")
     * @param waiting alerts that could not be shown for lack of permission
     */
    fun prompt(canPost: Boolean, runtimePermission: Boolean, explained: Boolean, waiting: Int): NotifPrompt = when {
        canPost -> NotifPrompt.NONE
        runtimePermission && !explained -> NotifPrompt.EXPLAIN
        waiting > 0 -> NotifPrompt.WAITING
        else -> NotifPrompt.NONE
    }

    /**
     * What the button does. The system only shows its question while it is allowed to: never
     * asked, or denied once ([showRationale]). After "niet meer vragen" only the settings help.
     */
    fun action(runtimePermission: Boolean, granted: Boolean, askedSystem: Boolean, showRationale: Boolean): NotifAction = when {
        runtimePermission && !granted && (!askedSystem || showRationale) -> NotifAction.REQUEST
        else -> NotifAction.OPEN_SETTINGS
    }
}

// ---------------------------------------------------------------------------------------------
// Camera permission for the QR scanner.
// ---------------------------------------------------------------------------------------------

object CameraPolicy {
    /** After "niet meer vragen" the system shows nothing: "Camera toestaan" opens the app settings. */
    fun openSettings(granted: Boolean, askedBefore: Boolean, showRationale: Boolean): Boolean =
        !granted && askedBefore && !showRationale

    /** "Camera opnieuw proberen" only helps when the phone has a camera at all. */
    fun canRetry(issue: CameraIssue?, hasHardware: Boolean): Boolean = hasHardware && issue != null

    /**
     * The message for a CameraX state error code (CameraInfo.cameraState after binding): another
     * app holds the camera, it is switched off, or anything else (driver error, stream setup).
     */
    fun issueFor(errorCode: Int): CameraIssue = when (errorCode) {
        CameraState.ERROR_CAMERA_IN_USE, CameraState.ERROR_MAX_CAMERAS_IN_USE -> CameraIssue.IN_USE
        CameraState.ERROR_CAMERA_DISABLED -> CameraIssue.DISABLED
        else -> CameraIssue.UNAVAILABLE
    }
}

/**
 * Why the QR scanner shows a message instead of the camera picture. CameraX reports most of these
 * only through the camera's state after binding (CameraState.error), not as an exception.
 */
enum class CameraIssue {
    /** No camera, a driver error or another critical error. */
    UNAVAILABLE,

    /** Another app (a video call, the camera app) holds the camera. */
    IN_USE,

    /** Switched off on this phone (privacy setting or a device policy). */
    DISABLED,
}

// ---------------------------------------------------------------------------------------------
// System attention block on the start screen (verbeterlijst 22): worker heartbeat, safe mode,
// last backup and maintenance, as on the web overview (attentionItems in pages/Overview.tsx).
// ---------------------------------------------------------------------------------------------

enum class AttentionLevel { NONE, WARN, ERROR }

data class Attention(val items: List<StatusItem>, val level: AttentionLevel)

object AttentionUi {
    private val KEYS = setOf("worker", "safe_mode", "backup", "maintenance")

    /**
     * The status lines worth a block at the top: safe mode whenever the server reports it (it is
     * only sent while on), the others only when not "ok" (and not "unknown"). Integrations stay on
     * the web's Systeem page.
     */
    fun of(status: List<StatusItem>): Attention {
        val items = status
            .filter { it.key == "safe_mode" || (it.key in KEYS && it.state != "ok" && it.state != "unknown") }
            .sortedBy { if (it.state == "error") 0 else 1 }
        val level = when {
            items.isEmpty() -> AttentionLevel.NONE
            items.any { it.state == "error" } -> AttentionLevel.ERROR
            else -> AttentionLevel.WARN
        }
        return Attention(items, level)
    }
}

// ---------------------------------------------------------------------------------------------
// Compact system line on the start screen, always shown with an overview (also when all is well):
// "Laatste back-up 5 uur geleden · Achtergronddienst actief" (+ "Veilige modus aan"). Problems
// also keep their prominent attention block right below it.
// ---------------------------------------------------------------------------------------------

enum class WorkerHealth {
    /** Recent heartbeat: devices are read and the climate control runs. */
    ACTIVE,

    /** Paused on purpose while a restore runs (maintenance). */
    PAUSED,

    /** No (recent) heartbeat. */
    DOWN,
}

sealed interface BackupAge {
    /** The server has no good backup yet. */
    data object None : BackupAge

    /** The last good backup was made [ago]. */
    data class Ago(val ago: RelativeTime) : BackupAge
}

data class SystemLine(
    /** Null when the server does not report it; that part is left out. */
    val backup: BackupAge?,
    /** Null when the server does not report it; that part is left out. */
    val worker: WorkerHealth?,
    val safeMode: Boolean,
    /** Colour of the dot: the same level as the attention block (NONE = all well). */
    val level: AttentionLevel,
) {
    val isEmpty: Boolean get() = backup == null && worker == null && !safeMode
}

object SystemLineUi {
    /** Heartbeat age below which a worker without a usable state still counts as running (server: 120 s). */
    private const val WORKER_OK_S = 120.0

    fun of(status: List<StatusItem>): SystemLine {
        val maintenance = status.any { it.key == "maintenance" }
        val worker = status.firstOrNull { it.key == "worker" }?.let { w ->
            val beatAge = w.ageS // a local: no smart cast on a property from :core
            when {
                maintenance -> WorkerHealth.PAUSED
                w.state == "ok" -> WorkerHealth.ACTIVE
                w.state == "error" -> WorkerHealth.DOWN
                // Without a usable state, the heartbeat age decides (as the server does).
                beatAge != null -> if (beatAge < WORKER_OK_S) WorkerHealth.ACTIVE else WorkerHealth.DOWN
                // "warn" without a heartbeat age is the worker paused for a restore.
                w.state == "warn" -> WorkerHealth.PAUSED
                else -> null
            }
        }
        val backup = status.firstOrNull { it.key == "backup" }?.let { b ->
            val hours = b.ageH
            when {
                hours != null -> BackupAge.Ago(RelativeTime.ofAge(Duration.ofSeconds((hours * 3600).roundToLong())))
                b.state == "warn" || b.state == "error" -> BackupAge.None
                else -> null
            }
        }
        return SystemLine(
            backup = backup,
            worker = worker,
            safeMode = status.any { it.key == "safe_mode" },
            level = AttentionUi.of(status).level,
        )
    }

    /** "Laatste back-up 5 uur geleden · Achtergronddienst actief · Veilige modus aan". */
    fun text(t: TextSource, line: SystemLine): String = listOfNotNull(
        when (val b = line.backup) {
            null -> null
            BackupAge.None -> t.string(R.string.system_backup_none)
            is BackupAge.Ago -> t.string(R.string.system_backup_ago, CoreTexts.relative(t, b.ago))
        },
        when (line.worker) {
            null -> null
            WorkerHealth.ACTIVE -> t.string(R.string.system_worker_active)
            WorkerHealth.PAUSED -> t.string(R.string.system_worker_paused)
            WorkerHealth.DOWN -> t.string(R.string.system_worker_down)
        },
        if (line.safeMode) t.string(R.string.system_safe_mode) else null,
    ).joinToString(" · ")
}

// ---------------------------------------------------------------------------------------------
// Light groups on the start screen.
// ---------------------------------------------------------------------------------------------

object LightsUi {
    /** Hidden groups are left out; the bridge's "Alle lampen" group comes last. */
    fun visible(groups: List<LightGroup>): List<LightGroup> =
        groups.filterNot { it.hidden }.sortedBy { if (it.allLights) 1 else 0 }
}
