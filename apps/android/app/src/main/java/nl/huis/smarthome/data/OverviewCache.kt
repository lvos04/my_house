package nl.huis.smarthome.data

import android.content.Context
import kotlinx.serialization.Serializable
import nl.huis.smarthome.core.Overview
import nl.huis.smarthome.core.ShcJson
import java.io.File
import java.io.IOException

/**
 * Last known overview on disk. Used to show dimmed "last known" values while
 * offline, and read by the home-screen widget. Contains no secrets.
 */
@Serializable
data class CachedOverview(val savedAt: String, val overview: Overview)

object OverviewCache {
    private const val FILE = "overview_cache.json"

    fun read(context: Context): CachedOverview? = try {
        val f = File(context.filesDir, FILE)
        if (!f.exists()) null else ShcJson.decodeFromString(CachedOverview.serializer(), f.readText())
    } catch (e: IOException) {
        null
    } catch (e: IllegalArgumentException) {
        null
    }

    fun write(context: Context, cached: CachedOverview) {
        try {
            val tmp = File(context.filesDir, "$FILE.tmp")
            tmp.writeText(ShcJson.encodeToString(CachedOverview.serializer(), cached))
            tmp.renameTo(File(context.filesDir, FILE))
        } catch (e: IOException) {
            // Cache is best effort.
        }
    }

    fun clear(context: Context) {
        File(context.filesDir, FILE).delete()
    }
}
