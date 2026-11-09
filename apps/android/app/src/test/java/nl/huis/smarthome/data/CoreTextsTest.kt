package nl.huis.smarthome.data

import nl.huis.smarthome.R
import nl.huis.smarthome.core.AddressProblem
import nl.huis.smarthome.core.ApiProblem
import nl.huis.smarthome.core.CommandLabel
import nl.huis.smarthome.core.ConnectionChange
import nl.huis.smarthome.core.PairError
import nl.huis.smarthome.core.UnpairReason
import org.junit.Assert.assertEquals
import org.junit.Assume.assumeTrue
import org.junit.Test
import org.w3c.dom.Element
import java.io.File
import java.time.DayOfWeek
import java.time.Month
import javax.xml.parsers.DocumentBuilderFactory

/**
 * The codes that :core reports instead of Dutch sentences (verbeterlijst, kleine punten
 * "Toegankelijkheid Android") each have their own strings.xml text, and that text says the same
 * as the Dutch fallback in :core, so JVM callers, tests and the app never drift apart.
 */
class CoreTextsTest {
    @Test
    fun everyCodeHasItsOwnText() {
        assertDistinct(ApiProblem.entries.map { CoreTexts.of(it) })
        assertDistinct(CommandLabel.entries.map { CoreTexts.of(it) })
        assertDistinct(AddressProblem.entries.map { CoreTexts.of(it) })
        assertDistinct(ConnectionChange.entries.map { CoreTexts.of(it) })
        assertDistinct(PairError.entries.map { CoreTexts.of(it) })
        assertDistinct(UnpairReason.entries.map { CoreTexts.of(it) })
        // Short and spoken day names (week schedule) and month names ("3 nov 09:12").
        assertDistinct(DayOfWeek.entries.flatMap { listOf(CoreTexts.day(it), CoreTexts.day(it, spoken = true)) })
        assertDistinct(Month.entries.map { CoreTexts.month(it) })
    }

    @Test
    fun stringsXmlSaysTheSameAsCore() {
        val file = File("src/main/res/values/strings.xml")
        // Android unit tests run in the module directory; elsewhere there is nothing to compare.
        assumeTrue(file.exists())
        val strings = readStrings(file)
        fun check(code: Enum<*>, id: Int, dutch: String) {
            val name = resourceName(id)
            assertEquals("${code::class.simpleName}.${code.name} ($name)", dutch, strings[name])
        }
        ApiProblem.entries.forEach { check(it, CoreTexts.of(it), it.dutch.replace("%d", "%1\$d")) }
        CommandLabel.entries.forEach { check(it, CoreTexts.of(it), it.dutch) }
        AddressProblem.entries.forEach { check(it, CoreTexts.of(it), it.dutch) }
        ConnectionChange.entries.forEach { check(it, CoreTexts.of(it), it.dutch) }
        PairError.entries.forEach { check(it, CoreTexts.of(it), it.dutch) }
        UnpairReason.entries.forEach { check(it, CoreTexts.of(it), it.message) }
    }

    @Test
    fun onlyTheServerErrorTextTakesTheStatus() {
        val file = File("src/main/res/values/strings.xml")
        assumeTrue(file.exists())
        val strings = readStrings(file)
        for (p in ApiProblem.entries) {
            val text = strings.getValue(resourceName(CoreTexts.of(p)))
            assertEquals(p.name, p.withStatus, text.contains("%1\$d"))
        }
    }

    private fun assertDistinct(ids: List<Int>) = assertEquals(ids.size, ids.toSet().size)

    private fun resourceName(id: Int): String =
        R.string::class.java.fields.firstOrNull { it.getInt(null) == id }?.name ?: error("no R.string field for $id")

    /** `<string name="…">` texts, with Android's backslash escapes undone. */
    private fun readStrings(file: File): Map<String, String> {
        val doc = DocumentBuilderFactory.newInstance().newDocumentBuilder().parse(file)
        val nodes = doc.getElementsByTagName("string")
        return (0 until nodes.length).associate { i ->
            val e = nodes.item(i) as Element
            e.getAttribute("name") to e.textContent.replace("\\'", "'").replace("\\\"", "\"")
        }
    }
}
