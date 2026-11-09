package nl.huis.smarthome.ui

import androidx.annotation.DrawableRes
import androidx.annotation.StringRes
import nl.huis.smarthome.R
import nl.huis.smarthome.core.CommandLabel
import nl.huis.smarthome.core.Quality
import nl.huis.smarthome.core.RouteKind
import nl.huis.smarthome.data.HomeState

// ---------------------------------------------------------------------------------------------
// The design language Peil (docs/ontwerp/PEIL_MIGRATIE.md §11.2): a status is always an icon, a
// fixed word and a shape, never colour alone. Decided here without Compose so it can be unit-tested;
// the composables in ui/components/Peil.kt only draw it.
// ---------------------------------------------------------------------------------------------

/** Health words of Peil (ONTWERPTAAL §11.1) with their lucide icon; [dashed] = dashed border (not measured). */
enum class PeilStatus(@StringRes val woord: Int, @DrawableRes val icoon: Int, val dashed: Boolean) {
    /** Measured and good: the only place where green is allowed. */
    GEZOND(R.string.status_healthy, R.drawable.lucide_circle_check, false),
    BEPERKT(R.string.status_degraded, R.drawable.lucide_triangle_alert, false),
    STORING(R.string.status_down, R.drawable.lucide_octagon_x, false),

    /** Not measured (unreachable, no answer): dashed, never red. */
    ONBEKEND(R.string.status_unknown, R.drawable.lucide_circle_help, true),

    /** Last known value, older than it should be: dashed and hatched instead of dimmed. */
    VEROUDERD(R.string.status_stale, R.drawable.lucide_clock_alert, true),
    GEPAUZEERD(R.string.status_paused, R.drawable.lucide_circle_pause, false),
    NIET_GEKOPPELD(R.string.status_unlinked, R.drawable.lucide_link_2_off, true),
}

/** Severity of an alert (ErnstLabel, T-20): a word with a thick bar, not a health status. */
enum class Ernst(@StringRes val woord: Int) {
    INFO(R.string.severity_info),
    MIDDEL(R.string.severity_medium),
    HOOG(R.string.severity_high),
}

/** Kind of note (Melding): info, a limitation, a failure, unknown, a policy gate or neutral. */
enum class MeldingSoort(@DrawableRes val icoon: Int) {
    INFO(R.drawable.lucide_info),
    BEPERKT(R.drawable.lucide_triangle_alert),
    STORING(R.drawable.lucide_octagon_x),
    ONBEKEND(R.drawable.lucide_circle_help),

    /** Something the rules do not allow (blocked mode, shared outdoor unit): a lock. */
    GATE(R.drawable.lucide_lock),
    NEUTRAAL(R.drawable.lucide_info),
}

/** How the status line of a command looks: icon and tone. Done = neutral check, never green. */
enum class OpdrachtTint { NEUTRAAL, BEZIG, BEPERKT, STORING }

data class OpdrachtVorm(@DrawableRes val icoon: Int, val tint: OpdrachtTint)

/** Which route the app uses: neutral (connected ≠ healthy); no route is Onbekend. */
enum class RouteVorm(@StringRes val tekst: Int, @DrawableRes val icoon: Int, val dashed: Boolean) {
    THUIS(R.string.route_lan, R.drawable.lucide_house, false),
    ONDERWEG(R.string.route_remote, R.drawable.lucide_globe, false),
    GEEN(R.string.route_none, R.drawable.lucide_wifi_off, true),
}

object PeilUi {
    /**
     * The status line on Start: all well = Gezond, otherwise the level of the attention block. Without a
     * connection the line is the last known state, and that is never green (Peil §4.4): Gezond becomes
     * Verouderd; Beperkt and Storing stay, so a known problem does not disappear when the server is gone.
     */
    fun systeem(level: AttentionLevel, verouderd: Boolean = false): PeilStatus = when (level) {
        AttentionLevel.NONE -> if (verouderd) PeilStatus.VEROUDERD else PeilStatus.GEZOND
        AttentionLevel.WARN -> PeilStatus.BEPERKT
        AttentionLevel.ERROR -> PeilStatus.STORING
    }

    /** A status item from the server (`ok`, `warn`, `error`, anything else = unknown). */
    fun item(state: String?): PeilStatus = when (state) {
        "ok" -> PeilStatus.GEZOND
        "warn" -> PeilStatus.BEPERKT
        "error" -> PeilStatus.STORING
        else -> PeilStatus.ONBEKEND
    }

    /** Alert severity from the server → fixed severity word (same as the web app). */
    fun ernst(severity: String?): Ernst = when (severity) {
        "error" -> Ernst.HOOG
        "warn" -> Ernst.MIDDEL
        else -> Ernst.INFO
    }

    /**
     * Icon and tone of a command line: on its way = loader, confirmed or sent = check/send in ink,
     * failed or refused = octagon-x (Storing), retrying, replaced or no confirmation in time = triangle-alert (Beperkt).
     */
    fun opdracht(line: CommandLine): OpdrachtVorm = when (line) {
        CommandLine.Sending -> OpdrachtVorm(R.drawable.lucide_loader_circle, OpdrachtTint.BEZIG)
        is CommandLine.Elsewhere -> OpdrachtVorm(R.drawable.lucide_loader_circle, OpdrachtTint.BEZIG)
        is CommandLine.Tracked -> when (line.label) {
            CommandLabel.QUEUED, CommandLabel.SENT_UNCONFIRMED -> OpdrachtVorm(R.drawable.lucide_loader_circle, OpdrachtTint.BEZIG)
            // A light command: the bridge has no readback, "Verzonden" is the end state.
            CommandLabel.SENT -> OpdrachtVorm(R.drawable.lucide_send, OpdrachtTint.NEUTRAAL)
            CommandLabel.CONFIRMED -> OpdrachtVorm(R.drawable.lucide_check, OpdrachtTint.NEUTRAAL)
            CommandLabel.FAILED, CommandLabel.DENIED -> OpdrachtVorm(R.drawable.lucide_octagon_x, OpdrachtTint.STORING)
            // Same as the web app (.c-status.retrying/.timeout/.cancelled): a limitation, not a failure.
            CommandLabel.RETRYING, CommandLabel.TIMEOUT, CommandLabel.CANCELLED ->
                OpdrachtVorm(R.drawable.lucide_triangle_alert, OpdrachtTint.BEPERKT)
        }
    }

    fun route(state: HomeState): RouteVorm = when {
        !state.online || state.route == null -> RouteVorm.GEEN
        state.route?.kind == RouteKind.LAN -> RouteVorm.THUIS
        else -> RouteVorm.ONDERWEG
    }

    /** Measured quality is a neutral label; only "geen meting" gets the Onbekend shape. */
    fun kwaliteitOnbekend(q: Quality): Boolean = q == Quality.MISSING

    /** Mode → its domain icon (flame, snowflake), or none for off/auto/dry/fan. */
    @DrawableRes
    fun modusIcoon(mode: String): Int? = when (mode) {
        "heat" -> R.drawable.lucide_flame
        "cool" -> R.drawable.lucide_snowflake
        else -> null
    }
}
