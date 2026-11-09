package nl.huis.smarthome.core

import java.text.DecimalFormat
import java.text.DecimalFormatSymbols
import java.time.Duration
import java.time.Instant
import java.time.LocalDate
import java.time.LocalTime
import java.time.ZoneId
import java.time.format.DateTimeFormatter
import java.time.format.DateTimeParseException
import java.time.temporal.ChronoUnit
import java.util.Locale
import kotlin.math.abs
import kotlin.math.min
import kotlin.math.roundToLong
import kotlin.random.Random

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

    fun clock(iso: String?, zone: ZoneId = ZoneId.of("Europe/Amsterdam")): String =
        parseInstant(iso)?.atZone(zone)?.format(DateTimeFormatter.ofPattern("HH:mm", nl)) ?: "–"

    /** "09:05": a 24-hour clock time, the same in every language. */
    fun hhmm(time: LocalTime): String = String.format(Locale.ROOT, "%02d:%02d", time.hour, time.minute)

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
// Relative time: :core decides how long ago, the app words it from strings.xml ("zojuist",
// "3 min geleden", "gisteren 14:05", "3 nov 09:12"), so no Dutch text lives here.
// ---------------------------------------------------------------------------

/** How long ago something happened, as numbers; the app turns it into text (strings.xml, plurals). */
sealed interface RelativeTime {
    /** No time, or one that cannot be read ("onbekend"). */
    data object Unknown : RelativeTime

    /** Just now, or a clock running slightly ahead ("zojuist"). */
    data object JustNow : RelativeTime

    /** "3 min geleden". */
    data class MinutesAgo(val minutes: Int) : RelativeTime

    /** "2 uur geleden". */
    data class HoursAgo(val hours: Int) : RelativeTime

    /** "3 dagen geleden"; only for an age without a moment ([ofAge]). */
    data class DaysAgo(val days: Int) : RelativeTime

    /** Earlier today, 6 hours or more ago ("vandaag 08:05"). [time] is in the home's time zone. */
    data class Today(val time: LocalTime) : RelativeTime

    /** "gisteren 14:05". */
    data class Yesterday(val time: LocalTime) : RelativeTime

    /** Before yesterday ("3 nov 09:12"). */
    data class OnDate(val date: LocalDate, val time: LocalTime) : RelativeTime

    companion object {
        private val AMSTERDAM: ZoneId = ZoneId.of("Europe/Amsterdam")

        /**
         * The moment [iso] (ISO 8601) seen from [now]: under 45 s just now, then minutes, then
         * whole hours up to 6 hours, then today / yesterday / a date with the time in [zone].
         */
        fun of(iso: String?, now: Instant = Instant.now(), zone: ZoneId = AMSTERDAM): RelativeTime {
            val at = NlFormat.parseInstant(iso) ?: return Unknown
            val secs = Duration.between(at, now).seconds
            return when {
                secs < 45 -> JustNow
                secs < 3600 -> MinutesAgo(((secs + 30) / 60).toInt())
                secs < 6 * 3600 -> HoursAgo((secs / 3600).toInt())
                else -> {
                    val local = at.atZone(zone)
                    val today = now.atZone(zone).toLocalDate()
                    val time = local.toLocalTime().truncatedTo(ChronoUnit.MINUTES)
                    when (val date = local.toLocalDate()) {
                        today -> Today(time)
                        today.minusDays(1) -> Yesterday(time)
                        else -> OnDate(date, time)
                    }
                }
            }
        }

        /**
         * An age without a moment, such as the server's "last backup 5.2 hours ago". Rounded as
         * the web app's ago(): under a minute just now, then minutes, hours (under 48), days.
         */
        fun ofAge(age: Duration?): RelativeTime {
            if (age == null) return Unknown
            val secs = age.seconds.coerceAtLeast(0)
            if (secs < 60) return JustNow
            val minutes = (secs / 60.0).roundToLong()
            if (minutes < 60) return MinutesAgo(minutes.toInt())
            val hours = (minutes / 60.0).roundToLong()
            if (hours < 48) return HoursAgo(hours.toInt())
            return DaysAgo((hours / 24.0).roundToLong().toInt())
        }
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

/** What to do with phone notifications after fetching the open alerts. */
data class NotifyPlan(
    /** New warnings/errors to show now. */
    val post: List<Alert>,
    /** Alert ids whose notification must disappear (resolved or acknowledged). */
    val cancel: Set<String>,
    /** Ids shown as a notification and still open: store this set. */
    val notified: Set<String>,
    /** Waiting until notifications are allowed; not lost, shown on a later run. */
    val pending: Set<String>,
)

object AlertNotifications {
    /**
     * An alert counts as notified only once it was really posted. Without permission it stays
     * pending and is shown on a later run as long as it is still open.
     */
    fun plan(open: List<Alert>, notified: Set<String>, canPost: Boolean): NotifyPlan {
        val openIds = open.filterNot { it.acknowledged }.map { it.id }.toSet()
        val candidates = AlertFilter.toNotify(open, notified)
        val post = if (canPost) candidates else emptyList()
        val pending = if (canPost) emptySet() else candidates.map { it.id }.toSet()
        return NotifyPlan(
            post = post,
            cancel = notified - openIds,
            notified = (notified intersect openIds) + post.map { it.id },
            pending = pending,
        )
    }
}

// ---------------------------------------------------------------------------
// Phone token lifetime (verbeterlijst 18): tokens expire after 180 days unless refreshed.
// ---------------------------------------------------------------------------

object TokenLifetime {
    val REFRESH_WINDOW: Duration = Duration.ofDays(30)
    val MIN_GAP: Duration = Duration.ofHours(6)

    /**
     * Refresh when the token expires within [REFRESH_WINDOW] (and has not expired yet: the server
     * would refuse). [lastAttempt] keeps a failing refresh from being retried on every request.
     */
    fun shouldRefresh(expiresAt: Instant?, now: Instant, lastAttempt: Instant? = null): Boolean {
        if (expiresAt == null || !expiresAt.isAfter(now)) return false
        if (Duration.between(now, expiresAt) > REFRESH_WINDOW) return false
        if (lastAttempt != null && Duration.between(lastAttempt, now) < MIN_GAP) return false
        return true
    }

    /** Read-only phone: not allowed to operate climate or lights. */
    fun canControl(userRole: String?, clientRole: String?): Boolean =
        (userRole == null || userRole == "admin") && (clientRole == null || clientRole == "admin" || clientRole == "control")
}

// ---------------------------------------------------------------------------
// Age of cached data (widget, offline view).
// ---------------------------------------------------------------------------

object Freshness {
    val STALE_AFTER: Duration = Duration.ofMinutes(30)

    /** Unknown or unreadable times count as stale. */
    fun isStale(savedAt: String?, now: Instant = Instant.now(), after: Duration = STALE_AFTER): Boolean {
        val at = NlFormat.parseInstant(savedAt) ?: return true
        return Duration.between(at, now) > after
    }

    /** How old [savedAt] is; the widget words it ("Bijgewerkt 3 min geleden", "Nog niet bijgewerkt"). */
    fun age(savedAt: String?, now: Instant = Instant.now(), zone: ZoneId = ZoneId.of("Europe/Amsterdam")): DataAge =
        if (NlFormat.parseInstant(savedAt) == null) DataAge.Never else DataAge.Updated(RelativeTime.of(savedAt, now, zone))
}

/** The age of cached data (widget footer), for the app to word from strings.xml. */
sealed interface DataAge {
    /** Nothing saved yet, or an unreadable time ("Nog niet bijgewerkt"). */
    data object Never : DataAge

    /** "Bijgewerkt 3 min geleden"; [ago] is never [RelativeTime.Unknown]. */
    data class Updated(val ago: RelativeTime) : DataAge
}
