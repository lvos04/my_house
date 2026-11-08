package nl.huis.smarthome.core

import java.text.DecimalFormat
import java.text.DecimalFormatSymbols
import java.time.Duration
import java.time.Instant
import java.time.ZoneId
import java.time.format.DateTimeFormatter
import java.time.format.DateTimeParseException
import java.util.Locale
import kotlin.math.abs
import kotlin.math.min
import kotlin.math.roundToLong
import kotlin.random.Random

// ---------------------------------------------------------------------------
// Connection route: LAN first, then the Cloudflare tunnel.
// ---------------------------------------------------------------------------

enum class RouteKind { LAN, REMOTE }

data class Route(val kind: RouteKind, val baseUrl: String)

object RouteSelector {
    /**
     * Probes the LAN address first (the caller uses a short timeout for it) and
     * falls back to the remote address. Returns null when neither answers.
     */
    suspend fun select(
        lanUrl: String?,
        remoteUrl: String?,
        probe: suspend (baseUrl: String, kind: RouteKind) -> Boolean,
    ): Route? {
        if (lanUrl != null && probe(lanUrl, RouteKind.LAN)) return Route(RouteKind.LAN, lanUrl)
        if (remoteUrl != null && probe(remoteUrl, RouteKind.REMOTE)) return Route(RouteKind.REMOTE, remoteUrl)
        return null
    }
}

// ---------------------------------------------------------------------------
// Dutch number/time formatting.
// ---------------------------------------------------------------------------

object NlFormat {
    private val nl = Locale.forLanguageTag("nl-NL")
    private val symbols = DecimalFormatSymbols(nl)

    private fun fmt(pattern: String) = DecimalFormat(pattern, symbols)

    fun temperature(c: Double?): String = c?.let { fmt("0.0").format(it) + "°" } ?: "–"

    fun percent(v: Double?): String = v?.let { fmt("0").format(it) + "%" } ?: "–"

    fun watts(w: Double?): String = w?.let {
        if (abs(it) >= 10_000) fmt("#,##0.0").format(it / 1000.0) + " kW" else fmt("#,##0").format(it) + " W"
    } ?: "–"

    fun kwh(v: Double?): String = v?.let {
        if (abs(it) >= 100) fmt("#,##0").format(it) + " kWh" else fmt("#,##0.0").format(it) + " kWh"
    } ?: "–"

    fun euro(v: Double?): String = v?.let {
        val s = fmt("#,##0.00").format(abs(it))
        if (it < 0) "−€ $s" else "€ $s"
    } ?: "–"

    fun parseInstant(iso: String?): Instant? = try {
        iso?.let { Instant.parse(it) }
    } catch (e: DateTimeParseException) {
        null
    }

    /** "zojuist", "3 min geleden", "2 uur geleden", "gisteren 14:05", "3 nov 09:12". */
    fun relative(iso: String?, now: Instant = Instant.now(), zone: ZoneId = ZoneId.of("Europe/Amsterdam")): String {
        val at = parseInstant(iso) ?: return "onbekend"
        val d = Duration.between(at, now)
        val secs = d.seconds
        return when {
            secs < 45 -> "zojuist"
            secs < 3600 -> "${(secs + 30) / 60} min geleden"
            secs < 6 * 3600 -> "${secs / 3600} uur geleden"
            else -> {
                val local = at.atZone(zone)
                val today = now.atZone(zone).toLocalDate()
                val time = local.format(DateTimeFormatter.ofPattern("HH:mm", nl))
                when (local.toLocalDate()) {
                    today -> "vandaag $time"
                    today.minusDays(1) -> "gisteren $time"
                    else -> local.format(DateTimeFormatter.ofPattern("d MMM HH:mm", nl))
                }
            }
        }
    }

    fun clock(iso: String?, zone: ZoneId = ZoneId.of("Europe/Amsterdam")): String =
        parseInstant(iso)?.atZone(zone)?.format(DateTimeFormatter.ofPattern("HH:mm", nl)) ?: "–"

    fun quality(q: Quality): String = when (q) {
        Quality.MEASURED -> "gemeten"
        Quality.DERIVED -> "afgeleid"
        Quality.ESTIMATED -> "geschat"
        Quality.MISSING -> "ontbreekt"
    }

    fun mode(mode: String): String = when (mode) {
        "off" -> "Uit"
        "heat" -> "Verwarmen"
        "cool" -> "Koelen"
        "auto" -> "Auto"
        "dry" -> "Drogen"
        "fan" -> "Ventileren"
        else -> mode
    }

    fun control(control: String): String = when (control) {
        "schedule" -> "Schema"
        "manual" -> "Handmatig"
        "oem_fallback" -> "Eigen regeling airco"
        "off" -> "Uit"
        else -> control
    }
}

// ---------------------------------------------------------------------------
// Setpoint handling.
// ---------------------------------------------------------------------------

object Setpoint {
    /** Applies [delta] to [current], rounded to the unit step and clamped to its range. */
    fun step(current: Double?, delta: Double, caps: ZoneCapabilities): Double {
        val step = if (caps.stepC > 0) caps.stepC else 0.5
        val base = current ?: ((caps.minC + caps.maxC) / 2)
        val raw = base + delta
        val rounded = (raw / step).roundToLong() * step
        return rounded.coerceIn(caps.minC, caps.maxC)
    }
}

/**
 * Tracks commands that were sent but not yet confirmed by a readback
 * (`zone.updated` with the requested values). The UI shows "Verzonden…" meanwhile.
 */
class PendingCommands(private val timeout: Duration = Duration.ofSeconds(30)) {
    data class Pending(val target: Double?, val mode: String?, val sentAt: Instant)

    private val pending = mutableMapOf<String, Pending>()

    @Synchronized
    fun sent(zoneId: String, target: Double?, mode: String?, now: Instant = Instant.now()) {
        pending[zoneId] = Pending(target, mode, now)
    }

    /** Returns true when the update confirms the pending command (which is then cleared). */
    @Synchronized
    fun onZoneUpdate(zone: Zone): Boolean {
        val p = pending[zone.id] ?: return false
        val targetOk = p.target == null || (zone.targetC != null && abs(zone.targetC - p.target) < 0.01)
        val modeOk = p.mode == null || zone.mode == p.mode
        if (targetOk && modeOk) {
            pending.remove(zone.id)
            return true
        }
        return false
    }

    @Synchronized
    fun isPending(zoneId: String, now: Instant = Instant.now()): Boolean {
        val p = pending[zoneId] ?: return false
        if (Duration.between(p.sentAt, now) > timeout) {
            pending.remove(zoneId)
            return false
        }
        return true
    }

    @Synchronized
    fun clear(zoneId: String) {
        pending.remove(zoneId)
    }
}

// ---------------------------------------------------------------------------
// Reconnect backoff.
// ---------------------------------------------------------------------------

class Backoff(
    private val initialMs: Long = 1_000,
    private val maxMs: Long = 60_000,
    private val random: Random = Random.Default,
) {
    private var attempt = 0

    fun nextDelayMs(): Long {
        val exp = initialMs * (1L shl min(attempt, 16))
        attempt++
        val capped = min(exp, maxMs)
        // +/- 20% jitter so several phones don't reconnect in lockstep.
        val jitter = (capped * 0.2 * (random.nextDouble() * 2 - 1)).toLong()
        return (capped + jitter).coerceIn(initialMs / 2, maxMs)
    }

    fun reset() {
        attempt = 0
    }
}

// ---------------------------------------------------------------------------
// Alerts that should become a phone notification.
// ---------------------------------------------------------------------------

object AlertFilter {
    private val notifySeverities = setOf("warn", "error")

    fun toNotify(alerts: List<Alert>, alreadyNotified: Set<String>): List<Alert> =
        alerts.filter { it.severity in notifySeverities && !it.acknowledged && it.id !in alreadyNotified }
}
