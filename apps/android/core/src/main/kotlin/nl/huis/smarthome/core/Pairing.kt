package nl.huis.smarthome.core

import kotlinx.serialization.Serializable
import kotlinx.serialization.SerializationException
import okhttp3.HttpUrl
import okhttp3.HttpUrl.Companion.toHttpUrlOrNull

/** Contents of the pairing QR code shown by the web app (see docs/api.md). */
@Serializable
data class PairPayload(
    val v: Int,
    val type: String,
    val code: String,
    val lan: String? = null,
    val remote: String? = null,
    val home: String? = null,
)

sealed interface PairParse {
    data class Ok(val code: String, val lanUrl: String?, val remoteUrl: String?, val homeName: String?) : PairParse
    data class Invalid(val reason: String) : PairParse
}

object PairingCodec {
    private const val TYPE = "shc-pair"

    fun parse(text: String): PairParse {
        val payload = try {
            ShcJson.decodeFromString(PairPayload.serializer(), text.trim())
        } catch (e: SerializationException) {
            return PairParse.Invalid("Dit is geen koppelcode van je Huis-app.")
        } catch (e: IllegalArgumentException) {
            return PairParse.Invalid("Dit is geen koppelcode van je Huis-app.")
        }
        if (payload.type != TYPE) return PairParse.Invalid("Dit is geen koppelcode van je Huis-app.")
        if (payload.v != 1) return PairParse.Invalid("Deze koppelcode is van een nieuwere versie. Werk de app bij.")
        val code = normalizeCode(payload.code) ?: return PairParse.Invalid("De koppelcode ontbreekt.")
        val lan = payload.lan?.let { UrlTools.normalizeBaseUrl(it) }
        val remote = payload.remote?.let { UrlTools.normalizeBaseUrl(it) }
        if (lan == null && remote == null) return PairParse.Invalid("De QR-code bevat geen serveradres.")
        if (remote != null && !remote.startsWith("https://")) {
            return PairParse.Invalid("Het externe adres moet https gebruiken.")
        }
        return PairParse.Ok(code, lan, remote, payload.home)
    }

    /** Uppercases, strips spaces; accepts "k7q4 9xpm" or "K7Q4-9XPM". */
    fun normalizeCode(raw: String): String? {
        val cleaned = raw.trim().uppercase().replace(" ", "")
        if (cleaned.length < 4 || cleaned.length > 32) return null
        if (!cleaned.all { it.isLetterOrDigit() || it == '-' }) return null
        return cleaned
    }
}

object UrlTools {
    /**
     * Turns user input like "192.168.1.20:8080", "https://huis.example.nl/" or
     * "https://huis.example.nl/api/v1" into a clean base URL without trailing slash
     * or API prefix. Returns null when it is not a usable http(s) URL.
     */
    fun normalizeBaseUrl(input: String): String? {
        var s = input.trim()
        if (s.isEmpty()) return null
        if (!s.contains("://")) s = "http://$s"
        val url = s.toHttpUrlOrNull() ?: return null
        if (url.scheme != "http" && url.scheme != "https") return null
        val segments = url.pathSegments.filter { it.isNotEmpty() }.toMutableList()
        if (segments.size >= 2 && segments[segments.size - 2] == "api" && segments.last() == "v1") {
            segments.removeAt(segments.size - 1)
            segments.removeAt(segments.size - 1)
        }
        val builder = HttpUrl.Builder().scheme(url.scheme).host(url.host)
        if (url.port != HttpUrl.defaultPort(url.scheme)) builder.port(url.port)
        segments.forEach { builder.addPathSegment(it) }
        return builder.build().toString().trimEnd('/')
    }

    fun api(base: String, path: String): String = base.trimEnd('/') + "/api/v1/" + path.trimStart('/')

    /** http -> ws, https -> wss. */
    fun webSocketUrl(base: String, homeId: String?): String {
        val wsBase = when {
            base.startsWith("https://") -> "wss://" + base.removePrefix("https://")
            base.startsWith("http://") -> "ws://" + base.removePrefix("http://")
            else -> base
        }.trimEnd('/')
        val q = homeId?.let { "?home_id=$it" } ?: ""
        return "$wsBase/api/v1/ws$q"
    }
}
