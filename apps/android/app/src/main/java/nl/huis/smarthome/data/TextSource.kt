package nl.huis.smarthome.data

import android.content.Context
import android.content.res.Resources
import androidx.annotation.PluralsRes
import androidx.annotation.StringRes

/**
 * Where the formatters in [CoreTexts] get their strings.xml texts: the app's [Resources], or in
 * the JVM unit tests a reader of strings.xml itself, so the Dutch result can be checked there.
 */
interface TextSource {
    fun string(@StringRes id: Int, vararg args: Any): String

    /** A `<plurals>` text for [count]; [args] fill in its placeholders (usually the count again). */
    fun plural(@PluralsRes id: Int, count: Int, vararg args: Any): String

    companion object {
        fun of(context: Context): TextSource = of(context.resources)

        fun of(res: Resources): TextSource = object : TextSource {
            override fun string(id: Int, vararg args: Any): String =
                if (args.isEmpty()) res.getString(id) else res.getString(id, *args)

            override fun plural(id: Int, count: Int, vararg args: Any): String =
                res.getQuantityString(id, count, *args)
        }
    }
}
