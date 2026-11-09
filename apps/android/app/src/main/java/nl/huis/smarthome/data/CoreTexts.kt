package nl.huis.smarthome.data

import android.content.Context
import androidx.annotation.StringRes
import nl.huis.smarthome.R
import nl.huis.smarthome.core.AddressProblem
import nl.huis.smarthome.core.ApiException
import nl.huis.smarthome.core.ApiProblem
import nl.huis.smarthome.core.CommandLabel
import nl.huis.smarthome.core.ConnectionChange
import nl.huis.smarthome.core.DataAge
import nl.huis.smarthome.core.DayPart
import nl.huis.smarthome.core.DayParts
import nl.huis.smarthome.core.NlFormat
import nl.huis.smarthome.core.PairError
import nl.huis.smarthome.core.PairParse
import nl.huis.smarthome.core.RelativeTime
import nl.huis.smarthome.core.TrackedCommand
import nl.huis.smarthome.core.UnpairReason
import java.time.DayOfWeek
import java.time.Month

/**
 * strings.xml texts for the outcomes that the pure-Kotlin :core module reports as codes, so that
 * every message the app shows comes from resources (TalkBack, translations). The `when`s are
 * exhaustive: a new code in :core does not compile until it has a text here.
 */
object CoreTexts {
    @StringRes
    fun of(problem: AddressProblem): Int = when (problem) {
        AddressProblem.LAN_INVALID -> R.string.address_lan_invalid
        AddressProblem.LAN_SCHEME -> R.string.address_lan_scheme
        AddressProblem.LAN_NOT_HOME -> R.string.address_lan_not_home
        AddressProblem.REMOTE_INVALID -> R.string.address_remote_invalid
        AddressProblem.REMOTE_NOT_HTTPS -> R.string.address_remote_not_https
        AddressProblem.REMOTE_IS_HOME -> R.string.address_remote_is_home
    }

    @StringRes
    fun of(change: ConnectionChange): Int = when (change) {
        ConnectionChange.LOST -> R.string.connection_lost
        ConnectionChange.RESTARTING -> R.string.connection_restarting
        ConnectionChange.WRONG_SERVER -> R.string.connection_wrong_server
        ConnectionChange.RESTORED -> R.string.connection_restored
    }

    @StringRes
    fun of(error: PairError): Int = when (error) {
        PairError.NOT_OURS -> R.string.pair_not_our_code
        PairError.NEWER_VERSION -> R.string.pair_newer_version
        PairError.CODE_MISSING -> R.string.pair_code_missing
        PairError.QR_LAN_INVALID -> R.string.pair_qr_lan_invalid
        PairError.QR_REMOTE_INVALID -> R.string.pair_qr_remote_invalid
        PairError.QR_NO_ADDRESS -> R.string.pair_qr_no_address
        PairError.ADDRESS_INVALID -> R.string.pair_invalid_address
        PairError.CODE_INVALID -> R.string.pair_invalid_code
        PairError.NEEDS_HTTPS -> R.string.pair_needs_https
        PairError.ADDRESS_RULE -> R.string.pair_address_rule
    }

    /** A refused QR code or manual entry: the broken address rule when there is one. */
    @StringRes
    fun of(invalid: PairParse.Invalid): Int = invalid.address?.let { of(it) } ?: of(invalid.error)

    @StringRes
    fun of(reason: UnpairReason): Int = when (reason) {
        UnpairReason.REVOKED -> R.string.unpaired_reason_revoked
        UnpairReason.EXPIRED -> R.string.unpaired_reason_expired
        UnpairReason.UNKNOWN_TOKEN -> R.string.unpaired_reason_unknown_token
        UnpairReason.STORE_RESET -> R.string.unpaired_reason_store_reset
        UnpairReason.UPGRADE -> R.string.unpaired_reason_upgrade
    }

    /** The status line under a zone or light group (verbeterlijst 23). */
    @StringRes
    fun of(label: CommandLabel): Int = when (label) {
        CommandLabel.QUEUED -> R.string.command_queued
        CommandLabel.RETRYING -> R.string.command_retrying
        CommandLabel.SENT -> R.string.command_sent
        CommandLabel.SENT_UNCONFIRMED -> R.string.command_sent_unconfirmed
        CommandLabel.CONFIRMED -> R.string.command_confirmed
        CommandLabel.FAILED -> R.string.command_failed
        CommandLabel.DENIED -> R.string.command_denied
        CommandLabel.CANCELLED -> R.string.command_cancelled
        CommandLabel.TIMEOUT -> R.string.command_timeout
    }

    /**
     * A failure the client describes itself (Cloudflare, tunnel, redirect, bare status code).
     * [ApiProblem.withStatus] texts take the HTTP status as `%1$d`: use [text] or pass it along.
     */
    @StringRes
    fun of(problem: ApiProblem): Int = when (problem) {
        ApiProblem.UNVERIFIED_ROUTE -> R.string.api_unverified_route
        ApiProblem.CLEARTEXT_REFUSED -> R.string.api_cleartext_refused
        ApiProblem.BAD_RESPONSE -> R.string.api_bad_response
        ApiProblem.NOT_API -> R.string.api_not_api
        ApiProblem.CF_ACCESS_LOGIN -> R.string.api_cf_access_login
        ApiProblem.CF_FORBIDDEN -> R.string.api_cf_forbidden
        ApiProblem.REDIRECT -> R.string.api_redirect
        ApiProblem.TUNNEL_UNAVAILABLE -> R.string.api_tunnel_unavailable
        ApiProblem.SERVER_UNAVAILABLE -> R.string.api_server_unavailable
        ApiProblem.REPAIR_NEEDED -> R.string.api_repair_needed
        ApiProblem.NO_RIGHTS -> R.string.api_no_rights
        ApiProblem.NOT_FOUND -> R.string.api_not_found
        ApiProblem.CONFLICT -> R.string.api_conflict
        ApiProblem.TOO_LARGE -> R.string.api_too_large
        ApiProblem.TOO_MANY_REQUESTS -> R.string.api_too_many_requests
        ApiProblem.SERVER_ERROR -> R.string.api_server_error
    }

    /** The strings.xml text for [problem]; [status] fills in "Serverfout (…)". */
    fun text(context: Context, problem: ApiProblem, status: Int = 0): String =
        if (problem.withStatus) context.getString(of(problem), status) else context.getString(of(problem))

    /**
     * What to tell the user about a failed request: the strings.xml text when the client described
     * the failure itself, the server's own message otherwise, and null for anything else (the
     * caller shows its generic text rather than a technical exception message).
     */
    fun message(context: Context, e: Throwable): String? {
        if (e !is ApiException) return null
        return e.problem?.let { text(context, it, e.status) } ?: e.message.takeIf { it.isNotBlank() }
    }

    /** "Bevestigd", "Mislukt: <reden van de server>" … for a command of this phone. */
    fun command(context: Context, c: TrackedCommand): String {
        val label = context.getString(of(c.labelCode))
        return c.reason?.let { context.getString(R.string.command_with_reason, label, it) } ?: label
    }

    // -----------------------------------------------------------------------------------------
    // Times and days that :core reports as numbers (RelativeTime, DataAge, DayParts).
    // -----------------------------------------------------------------------------------------

    /** "zojuist", "3 min geleden", "2 uur geleden", "vandaag 08:05", "gisteren 14:05", "3 nov 09:12". */
    fun relative(t: TextSource, time: RelativeTime): String = when (time) {
        RelativeTime.Unknown -> t.string(R.string.time_unknown)
        RelativeTime.JustNow -> t.string(R.string.time_just_now)
        is RelativeTime.MinutesAgo -> t.plural(R.plurals.time_minutes_ago, time.minutes, time.minutes)
        is RelativeTime.HoursAgo -> t.plural(R.plurals.time_hours_ago, time.hours, time.hours)
        is RelativeTime.DaysAgo -> t.plural(R.plurals.time_days_ago, time.days, time.days)
        is RelativeTime.Today -> t.string(R.string.time_today, NlFormat.hhmm(time.time))
        is RelativeTime.Yesterday -> t.string(R.string.time_yesterday, NlFormat.hhmm(time.time))
        is RelativeTime.OnDate ->
            t.string(R.string.time_on_date, time.date.dayOfMonth, t.string(month(time.date.month)), NlFormat.hhmm(time.time))
    }

    /** Widget footer: "Bijgewerkt 3 min geleden" or "Nog niet bijgewerkt". */
    fun age(t: TextSource, age: DataAge): String = when (age) {
        DataAge.Never -> t.string(R.string.updated_never)
        is DataAge.Updated -> t.string(R.string.updated_ago, relative(t, age.ago))
    }

    /**
     * Days of the week schedule: "elke dag", "ma–vr", "za en zo", "ma, wo en vr"; [spoken] gives
     * the full names for TalkBack ("maandag t/m vrijdag"). Empty for no days.
     */
    fun days(t: TextSource, days: DayParts, spoken: Boolean = false): String = when (days) {
        DayParts.None -> ""
        DayParts.EveryDay -> t.string(R.string.days_every_day)
        is DayParts.Listed -> {
            val parts = days.parts.map { part ->
                when (part) {
                    is DayPart.Single -> t.string(day(part.day, spoken))
                    is DayPart.Range -> t.string(
                        if (spoken) R.string.days_range_spoken else R.string.days_range,
                        t.string(day(part.first, spoken)),
                        t.string(day(part.last, spoken)),
                    )
                }
            }
            if (parts.size <= 1) {
                parts.firstOrNull().orEmpty()
            } else {
                t.string(R.string.list_and, parts.dropLast(1).joinToString(", "), parts.last())
            }
        }
    }

    /** "ma" … "zo", or with [spoken] "maandag" … "zondag". */
    @StringRes
    fun day(day: DayOfWeek, spoken: Boolean = false): Int = when (day) {
        DayOfWeek.MONDAY -> if (spoken) R.string.day_mon_spoken else R.string.day_mon
        DayOfWeek.TUESDAY -> if (spoken) R.string.day_tue_spoken else R.string.day_tue
        DayOfWeek.WEDNESDAY -> if (spoken) R.string.day_wed_spoken else R.string.day_wed
        DayOfWeek.THURSDAY -> if (spoken) R.string.day_thu_spoken else R.string.day_thu
        DayOfWeek.FRIDAY -> if (spoken) R.string.day_fri_spoken else R.string.day_fri
        DayOfWeek.SATURDAY -> if (spoken) R.string.day_sat_spoken else R.string.day_sat
        DayOfWeek.SUNDAY -> if (spoken) R.string.day_sun_spoken else R.string.day_sun
    }

    /** "jan" … "dec" for "3 nov 09:12". */
    @StringRes
    fun month(month: Month): Int = when (month) {
        Month.JANUARY -> R.string.month_jan
        Month.FEBRUARY -> R.string.month_feb
        Month.MARCH -> R.string.month_mar
        Month.APRIL -> R.string.month_apr
        Month.MAY -> R.string.month_may
        Month.JUNE -> R.string.month_jun
        Month.JULY -> R.string.month_jul
        Month.AUGUST -> R.string.month_aug
        Month.SEPTEMBER -> R.string.month_sep
        Month.OCTOBER -> R.string.month_oct
        Month.NOVEMBER -> R.string.month_nov
        Month.DECEMBER -> R.string.month_dec
    }
}
