package nl.huis.smarthome.ui.theme

import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.toArgb
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import kotlinx.serialization.json.int
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Assume.assumeTrue
import org.junit.Test
import java.io.File
import java.security.MessageDigest

/**
 * PeilTokens.kt is generated from design/ontwerptaal/tokens.json (tools/peil_tokens.py). Every
 * colour and type role must match the pinned copy exactly; a new Peil version without running the
 * generator fails here (docs/ontwerp/PEIL_MIGRATIE.md §11.1, drift).
 */
class PeilTokensTest {
    /** Android unit tests run in the module directory (apps/android/app). */
    private val file = File("../../../design/ontwerptaal/tokens.json")

    private fun tokens(): JsonObject {
        assumeTrue("tokens.json niet gevonden vanaf ${File(".").absolutePath}", file.exists())
        return Json.parseToJsonElement(file.readText()).jsonObject
    }

    private fun hex(c: Color): String = "#%06X".format(c.toArgb() and 0xFFFFFF)

    @Test
    fun generatedFromThePinnedCopy() {
        tokens()
        val sha = MessageDigest.getInstance("SHA-256").digest(file.readBytes()).joinToString("") { "%02x".format(it) }
        assertEquals("PeilTokens.kt hoort bij een andere tokens.json; draai tools/peil_tokens.py", sha, PeilTokens.TOKENS_SHA256)
        val versie = tokens()["\$extensions"]!!.jsonObject["nl.peil"]!!.jsonObject["versie"]!!.jsonPrimitive.content
        assertEquals(versie, PeilTokens.VERSIE)
    }

    @Test
    fun everyColourMatchesTokensJsonInBothModes() {
        val kleur = tokens()["kleur"]!!.jsonObject
        val licht = PeilTokens.perPad(PeilTokens.Licht)
        val donker = PeilTokens.perPad(PeilTokens.Donker)
        var count = 0
        for ((groep, inhoud) in kleur) {
            if (groep == "alias" || groep.startsWith("$")) continue
            for ((naam, token) in inhoud.jsonObject) {
                if (naam.startsWith("$")) continue
                val pad = "kleur.$groep.$naam"
                val t = token.jsonObject
                val l = t["\$value"]!!.jsonPrimitive.content.uppercase()
                val d = t["\$extensions"]!!.jsonObject["nl.peil"]!!.jsonObject["modes"]!!.jsonObject["donker"]!!.jsonPrimitive.content.uppercase()
                assertEquals("$pad (licht)", l, licht[pad]?.let(::hex))
                assertEquals("$pad (donker)", d, donker[pad]?.let(::hex))
                count++
            }
        }
        assertEquals("aantal kleuren in PeilTokens.kt", count, licht.size)
        assertTrue(count > 100)
    }

    @Test
    fun typeRolesMatchAndNothingIsBelow14sp() {
        val rollen = tokens()["typografie"]!!.jsonObject["rol"]!!.jsonObject
        for ((naam, rol) in rollen) {
            if (naam.startsWith("$")) continue
            val v = rol.jsonObject["\$value"]!!.jsonObject
            val size = v["fontSize"]!!.jsonPrimitive.content.removeSuffix("rem").toFloat() * 16
            val r = PeilTokens.rollen.getValue(naam)
            assertEquals(naam, size, r.grootteSp, 0.01f)
            assertEquals(naam, v["fontWeight"]!!.jsonPrimitive.int, r.gewicht)
        }
        // The app's Material typography: Peil roles only, minimum 14 sp (meta).
        val stijlen = with(HuisTypography) {
            listOf(displayLarge, displayMedium, displaySmall, headlineMedium, headlineSmall, titleLarge, titleMedium, titleSmall, bodyLarge, bodyMedium, bodySmall, labelLarge, labelMedium, labelSmall)
        }
        stijlen.forEach { assertTrue("${it.fontSize}", it.fontSize.value >= 14f) }
        assertEquals(16f, HuisTypography.bodyLarge.fontSize.value)
        assertEquals(24f, HuisTypography.headlineSmall.fontSize.value)
        assertEquals(30f, HuisTypography.displaySmall.fontSize.value)
        assertEquals(48f, HuisTypography.displayLarge.fontSize.value)
    }

    @Test
    fun oldHuisNamesTakeTheirValueFromPeil() {
        assertEquals(PeilTokens.Licht.background, LightHuis.ground)
        assertEquals(PeilTokens.Donker.card, DarkHuis.surface)
        assertEquals(PeilTokens.Licht.borderStrong, LightHuis.controlOutline)
        // Boiler is no longer orange (ΔE 2,8 from "verwarmen" in dark): chart-3.
        assertEquals(PeilTokens.Donker.chart3, DarkHuis.boiler)
        assertEquals(PeilTokens.Licht.staleInk, LightHuis.dimmed)
    }
}
