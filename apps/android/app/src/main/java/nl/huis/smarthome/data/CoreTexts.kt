package nl.huis.smarthome.data

import android.content.Context
import androidx.annotation.StringRes
import nl.huis.smarthome.R
import nl.huis.smarthome.core.AddressProblem
import nl.huis.smarthome.core.ApiException
import nl.huis.smarthome.core.ApiProblem
import nl.huis.smarthome.core.CommandLabel
import nl.huis.smarthome.core.ConnectionChange
import nl.huis.smarthome.core.PairError
import nl.huis.smarthome.core.PairParse
import nl.huis.smarthome.core.TrackedCommand
import nl.huis.smarthome.core.UnpairReason

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
}
