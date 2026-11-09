package nl.huis.smarthome.core

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.SerializationException
import okhttp3.HttpUrl
import okhttp3.HttpUrl.Companion.toHttpUrlOrNull

/**
 * Contents of the pairing QR code shown by the web app:
 * `{"v":1,"type":"shc-pair","code","lan","remote","home","server_id","role"}`.
 */
@Serializable
data class PairPayload(
    val v: Int,
    val type: String,
    val code: String,
    val lan: String? = null,
    val remote: String? = null,
    val home: String? = null,
    @SerialName("server_id") val serverId: String? = null,
    val role: String? = null,
)

sealed interface PairParse {
    data class Ok(
        val code: String,
        val lanUrl: String?,
        val remoteUrl: String?,
        val homeName: String?,
        /** Identity of the server that made the code; checked before the code is sent anywhere. */
        val serverId: String? = null,
        /** Role the phone will get: "control" (default), "viewer" or "admin". */
        val role: String? = null,
    ) : PairParse

    data class Invalid(val reason: String) : PairParse
}

object PairingCodec {
    private const val TYPE = "shc-pair"
    private const val NOT_OURS = "Dit is geen koppelcode van je Huis-app."

    fun parse(text: String): PairParse {
        val payload = try {
            ShcJson.decodeFromString(PairPayload.serializer(), text.trim())
        } catch (e: SerializationException) {
            return PairParse.Invalid(NOT_OURS)
        } catch (e: IllegalArgumentException) {
            return PairParse.Invalid(NOT_OURS)
        }
        if (payload.type != TYPE) return PairParse.Invalid(NOT_OURS)
        if (payload.v != 1) return PairParse.Invalid("Deze koppelcode is van een nieuwere versie. Werk de app bij.")
        val code = normalizeCode(payload.code) ?: return PairParse.Invalid("De koppelcode ontbreekt.")
        val lan = payload.lan?.takeIf { it.isNotBlank() }?.let {
            UrlTools.normalizeBaseUrl(it) ?: return PairParse.Invalid("Het thuisadres in de QR-code klopt niet.")
        }
        val remote = payload.remote?.takeIf { it.isNotBlank() }?.let {
            UrlTools.normalizeBaseUrl(it) ?: return PairParse.Invalid("Het externe adres in de QR-code klopt niet.")
        }
        if (lan == null && remote == null) return PairParse.Invalid("De QR-code bevat geen serveradres.")
        lan?.let { ServerUrls.lanProblem(it) }?.let { return PairParse.Invalid(it) }
        remote?.let { ServerUrls.remoteProblem(it) }?.let { return PairParse.Invalid(it) }
        val serverId = payload.serverId?.let {
            ServerIdentityCheck.normalize(it) ?: return PairParse.Invalid(NOT_OURS)
        }
        return PairParse.Ok(code, lan, remote, payload.home, serverId, payload.role)
    }

    /**
     * Manual entry (address + code). A home-network address (private IP, `.local`, …) becomes the
     * LAN address, http or https; any other address must use https and becomes the remote address.
     */
    fun manual(address: String, code: String): PairParse {
        val base = UrlTools.normalizeBaseUrl(address) ?: return PairParse.Invalid("Dit adres klopt niet.")
        val normalized = normalizeCode(code) ?: return PairParse.Invalid("Deze code klopt niet.")
        val host = base.toHttpUrlOrNull()?.host ?: return PairParse.Invalid("Dit adres klopt niet.")
        return when {
            HostRules.isPrivateHost(host) -> PairParse.Ok(normalized, base, null, null)
            base.startsWith("https://") -> {
                ServerUrls.remoteProblem(base)?.let { return PairParse.Invalid(it) }
                PairParse.Ok(normalized, null, base, null)
            }
            else -> PairParse.Invalid(
                "Een adres buiten je thuisnetwerk moet met https:// beginnen. Thuis kun je het IP-adres van je server gebruiken (bijvoorbeeld 192.168.1.20:8088).",
            )
        }
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
        if (url.username.isNotEmpty() || url.password.isNotEmpty()) return null
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

    fun host(base: String): String? = base.toHttpUrlOrNull()?.host

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
