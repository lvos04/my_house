package nl.huis.smarthome.data

import nl.huis.smarthome.R
import org.w3c.dom.Element
import java.io.File
import java.util.Locale
import javax.xml.parsers.DocumentBuilderFactory

/**
 * A [TextSource] that reads strings.xml itself, so JVM unit tests can check the Dutch text the
 * app shows (Android's Resources are not available there). Formats like Resources.getString:
 * without arguments the text as it is, with arguments through String.format.
 */
class StringsXmlTexts(file: File = FILE) : TextSource {
    private val strings: Map<String, String>
    private val plurals: Map<String, Map<String, String>>

    init {
        val doc = DocumentBuilderFactory.newInstance().newDocumentBuilder().parse(file)
        val s = doc.getElementsByTagName("string")
        strings = (0 until s.length).associate { i ->
            val e = s.item(i) as Element
            e.getAttribute("name") to unescape(e.textContent)
        }
        val p = doc.getElementsByTagName("plurals")
        plurals = (0 until p.length).associate { i ->
            val e = p.item(i) as Element
            val items = e.getElementsByTagName("item")
            e.getAttribute("name") to (0 until items.length).associate { j ->
                val item = items.item(j) as Element
                item.getAttribute("quantity") to unescape(item.textContent)
            }
        }
    }

    override fun string(id: Int, vararg args: Any): String {
        val name = name(R.string::class.java, id)
        val text = strings[name] ?: error("strings.xml has no <string name=\"$name\">")
        return if (args.isEmpty()) text else String.format(NL, text, *args)
    }

    override fun plural(id: Int, count: Int, vararg args: Any): String {
        val name = name(R.plurals::class.java, id)
        val items = plurals[name] ?: error("strings.xml has no <plurals name=\"$name\">")
        // Dutch (CLDR): "one" for exactly 1, "other" for every other number.
        val text = (if (count == 1) items["one"] else null) ?: items["other"] ?: error("$name has no \"other\" item")
        return String.format(NL, text, *args)
    }

    companion object {
        /** Android unit tests run in the module directory. */
        val FILE = File("src/main/res/values/strings.xml")

        private val NL: Locale = Locale.forLanguageTag("nl-NL")

        private fun name(cls: Class<*>, id: Int): String =
            cls.fields.firstOrNull { it.getInt(null) == id }?.name ?: error("no ${cls.simpleName} field for $id")

        /** Android's backslash escapes in strings.xml. */
        private fun unescape(text: String): String = text.replace("\\'", "'").replace("\\\"", "\"")
    }
}
