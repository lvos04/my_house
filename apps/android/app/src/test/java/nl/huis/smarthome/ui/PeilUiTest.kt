package nl.huis.smarthome.ui

import nl.huis.smarthome.R
import nl.huis.smarthome.core.CommandLabel
import nl.huis.smarthome.core.Quality
import nl.huis.smarthome.core.Route
import nl.huis.smarthome.core.RouteKind
import nl.huis.smarthome.data.HomeState
import nl.huis.smarthome.data.StringsXmlTexts
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Peil on Android (docs/ontwerp/PEIL_MIGRATIE.md §10, §11.2): status is always icon + fixed word +
 * shape, green only for measured health, and the Dutch words are the same as in the web app.
 */
class PeilUiTest {
    private val t = StringsXmlTexts()

    @Test
    fun fixedStatusWordsAndIconsAsInTheWebApp() {
        assertEquals(
            listOf("Gezond", "Beperkt", "Storing", "Onbekend", "Verouderd", "Gepauzeerd", "Niet gekoppeld"),
            PeilStatus.entries.map { t.string(it.woord) },
        )
        assertEquals(R.drawable.lucide_circle_check, PeilStatus.GEZOND.icoon)
        assertEquals(R.drawable.lucide_triangle_alert, PeilStatus.BEPERKT.icoon)
        assertEquals(R.drawable.lucide_octagon_x, PeilStatus.STORING.icoon)
        assertEquals(R.drawable.lucide_circle_help, PeilStatus.ONBEKEND.icoon)
        assertEquals(R.drawable.lucide_clock_alert, PeilStatus.VEROUDERD.icoon)
        // Not measured = dashed; measured states have a solid shape.
        assertEquals(setOf(PeilStatus.ONBEKEND, PeilStatus.VEROUDERD, PeilStatus.NIET_GEKOPPELD), PeilStatus.entries.filter { it.dashed }.toSet())
        // Every status has its own icon: the shape never depends on colour alone.
        assertEquals(PeilStatus.entries.size, PeilStatus.entries.map { it.icoon }.toSet().size)
    }

    @Test
    fun systemLineAndStatusItems() {
        assertEquals(PeilStatus.GEZOND, PeilUi.systeem(AttentionLevel.NONE))
        assertEquals(PeilStatus.BEPERKT, PeilUi.systeem(AttentionLevel.WARN))
        assertEquals(PeilStatus.STORING, PeilUi.systeem(AttentionLevel.ERROR))
        assertEquals(PeilStatus.GEZOND, PeilUi.item("ok"))
        assertEquals(PeilStatus.BEPERKT, PeilUi.item("warn"))
        assertEquals(PeilStatus.STORING, PeilUi.item("error"))
        assertEquals(PeilStatus.ONBEKEND, PeilUi.item("unknown"))
        assertEquals(PeilStatus.ONBEKEND, PeilUi.item(null))
    }

    @Test
    fun severityWords() {
        assertEquals(Ernst.HOOG, PeilUi.ernst("error"))
        assertEquals(Ernst.MIDDEL, PeilUi.ernst("warn"))
        assertEquals(Ernst.INFO, PeilUi.ernst("info"))
        assertEquals(Ernst.INFO, PeilUi.ernst(null))
        assertEquals(listOf("Info", "Middel", "Hoog"), Ernst.entries.map { t.string(it.woord) })
        assertEquals("Ernst: Hoog", t.string(R.string.severity_spoken, "Hoog"))
    }

    @Test
    fun commandLinesHaveAnIconAndDoneIsNeverGreen() {
        fun vorm(l: CommandLabel) = PeilUi.opdracht(CommandLine.Tracked(l, null, CommandUi.tone(nl.huis.smarthome.core.CommandStatus.CONFIRMED)))
        assertEquals(OpdrachtVorm(R.drawable.lucide_check, OpdrachtTint.NEUTRAAL), vorm(CommandLabel.CONFIRMED))
        assertEquals(OpdrachtVorm(R.drawable.lucide_loader_circle, OpdrachtTint.BEZIG), vorm(CommandLabel.QUEUED))
        assertEquals(OpdrachtVorm(R.drawable.lucide_loader_circle, OpdrachtTint.BEZIG), vorm(CommandLabel.SENT_UNCONFIRMED))
        assertEquals(OpdrachtVorm(R.drawable.lucide_send, OpdrachtTint.NEUTRAAL), vorm(CommandLabel.SENT))
        assertEquals(OpdrachtVorm(R.drawable.lucide_octagon_x, OpdrachtTint.STORING), vorm(CommandLabel.FAILED))
        assertEquals(OpdrachtVorm(R.drawable.lucide_octagon_x, OpdrachtTint.STORING), vorm(CommandLabel.DENIED))
        assertEquals(OpdrachtTint.BEPERKT, vorm(CommandLabel.TIMEOUT).tint)
        assertEquals(OpdrachtTint.BEPERKT, vorm(CommandLabel.RETRYING).tint)
        assertEquals(OpdrachtTint.BEPERKT, vorm(CommandLabel.CANCELLED).tint)
        assertEquals(OpdrachtTint.BEZIG, PeilUi.opdracht(CommandLine.Sending).tint)
        assertEquals(OpdrachtTint.BEZIG, PeilUi.opdracht(CommandLine.Elsewhere(sent = true)).tint)
        // Every label is covered (a new label must get a shape too).
        CommandLabel.entries.forEach { vorm(it) }
    }

    @Test
    fun routeIsNeutralAndNoRouteIsUnknown() {
        val lan = HomeState(online = true, route = Route(RouteKind.LAN, "http://192.168.1.20:8088", "s"))
        val remote = lan.copy(route = Route(RouteKind.REMOTE, "https://huis.voorbeeld.invalid", "s"))
        assertEquals(RouteVorm.THUIS, PeilUi.route(lan))
        assertEquals(RouteVorm.ONDERWEG, PeilUi.route(remote))
        assertEquals(RouteVorm.GEEN, PeilUi.route(lan.copy(online = false)))
        assertEquals(RouteVorm.GEEN, PeilUi.route(lan.copy(route = null)))
        assertFalse(RouteVorm.THUIS.dashed)
        assertTrue(RouteVorm.GEEN.dashed)
        assertEquals(listOf("Thuis", "Onderweg", "Geen verbinding"), RouteVorm.entries.map { t.string(it.tekst) })
    }

    @Test
    fun qualityWordsAndOnlyMissingIsUnknown() {
        assertTrue(PeilUi.kwaliteitOnbekend(Quality.MISSING))
        listOf(Quality.MEASURED, Quality.DERIVED, Quality.ESTIMATED).forEach { assertFalse(PeilUi.kwaliteitOnbekend(it)) }
        assertEquals("geen meting", t.string(R.string.quality_missing))
        assertEquals("gemeten", t.string(R.string.quality_measured))
    }

    @Test
    fun peilMicrocopy() {
        assertEquals("Wat actie nodig heeft", t.string(R.string.attention_title_warn))
        assertEquals("Storing in het systeem", t.string(R.string.attention_title_error))
        assertEquals("Laatst bekend · 15:47", t.string(R.string.stale_last_known, "15:47"))
        // Domain texts stay word for word (tests and users know them).
        assertEquals("Niet bereikbaar", t.string(R.string.zone_unreachable_pill))
        assertEquals("Bevestigd", t.string(R.string.command_confirmed))
    }

    @Test
    fun modeIcons() {
        assertEquals(R.drawable.lucide_flame, PeilUi.modusIcoon("heat"))
        assertEquals(R.drawable.lucide_snowflake, PeilUi.modusIcoon("cool"))
        assertNull(PeilUi.modusIcoon("off"))
        assertNull(PeilUi.modusIcoon("auto"))
    }
}
