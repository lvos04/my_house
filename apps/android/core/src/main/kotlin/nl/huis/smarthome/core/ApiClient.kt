package nl.huis.smarthome.core

import kotlinx.coroutines.suspendCancellableCoroutine
import kotlinx.serialization.KSerializer
import kotlinx.serialization.builtins.ListSerializer
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.contentOrNull
import okhttp3.Call
import okhttp3.Callback
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import okhttp3.Response
import java.io.IOException
import kotlin.coroutines.resumeWithException

/** Credentials of this phone. The Cloudflare Access headers only ever go to the remote address. */
data class Credentials(
    val token: String? = null,
    val cfAccessClientId: String? = null,
    val cfAccessClientSecret: String? = null,
) {
    val hasCfAccess: Boolean get() = !cfAccessClientId.isNullOrBlank() && !cfAccessClientSecret.isNullOrBlank()
}

/** Why the server no longer accepts this phone (verbeterlijst 18). */
enum class UnpairReason(val message: String) {
    REVOKED("De koppeling is ingetrokken. Koppel opnieuw met een QR-code uit de web-app (Systeem › Telefoon koppelen)."),
    EXPIRED("De koppeling is verlopen. Koppel opnieuw met een QR-code uit de web-app (Systeem › Telefoon koppelen)."),
    UNKNOWN_TOKEN("De server kent deze telefoon niet (meer), bijvoorbeeld na een herstel. Koppel opnieuw met een QR-code uit de web-app."),
    STORE_RESET("De beveiligde opslag op deze telefoon was beschadigd en is gewist. Koppel opnieuw met een QR-code uit de web-app."),
    UPGRADE("Koppel deze telefoon opnieuw, zodat de app je server kan herkennen voordat hij je sleutel stuurt.");

    val title: String get() = "Deze telefoon is niet meer gekoppeld"

    companion object {
        /** From an error code in an API answer or a WebSocket error frame. */
        fun fromCode(code: String?): UnpairReason? = when (code) {
            "token_revoked" -> REVOKED
            "token_expired" -> EXPIRED
            else -> null
        }
    }
}

/**
 * An HTTP answer that was not a success. [fromApi] is true when the body was the API's own JSON
 * error (`{"detail": …}`); false for proxy, Cloudflare or captive-portal pages.
 */
class ApiException(
    val status: Int,
    val code: String,
    override val message: String,
    val fromApi: Boolean = false,
) : IOException(message) {
    /** Set when the server says this phone is not paired any more. */
    val unpairReason: UnpairReason?
        get() = UnpairReason.fromCode(code)
            ?: if (fromApi && status == 401 && code == "auth_required") UnpairReason.UNKNOWN_TOKEN else null

    /** Kept for older callers: true when re-pairing is needed. */
    val isAuthError: Boolean get() = unpairReason != null

    /**
     * The route (address) is not usable right now: 5xx (incl. Cloudflare 52x/530 and nginx's
     * api_unavailable), a redirect, or a page that is not the API.
     */
    val routeProblem: Boolean
        get() = status in 500..599 || code == "api_unavailable" || code == "not_api" ||
            (!fromApi && status in 300..499)
}

/**
 * Small typed client for the Smart Home Control API. Every call names its [Route]:
 * the token is only attached to a route whose server identity was verified, and the
 * Cloudflare Access headers only to the remote route.
 */
class ApiClient(
    private val http: OkHttpClient,
    private val credentials: () -> Credentials,
    /** Plain http only to home-network hosts; tests on 127.0.0.1 pass `{ true }`. */
    private val cleartextAllowed: (String) -> Boolean = ServerUrls::cleartextAllowed,
) {
    private val jsonType = "application/json; charset=utf-8".toMediaType()

    // ---- Without login (before a route is known) ----

    suspend fun identity(base: String, kind: RouteKind, client: OkHttpClient = http): ServerIdentity =
        call(Route(kind, base), "GET", "pairing/identity", null, ServerIdentity.serializer(), auth = false, client = client)

    suspend fun redeem(base: String, kind: RouteKind, code: String, deviceName: String): RedeemResponse =
        call(
            Route(kind, base),
            "POST",
            "pairing/redeem",
            ShcJson.encodeToString(RedeemRequest.serializer(), RedeemRequest(code, deviceName)),
            RedeemResponse.serializer(),
            auth = false,
        )

    suspend fun health(base: String, kind: RouteKind, client: OkHttpClient = http): Health =
        call(Route(kind, base), "GET", "health", null, Health.serializer(), auth = false, client = client)

    // ---- With the phone token ----

    suspend fun me(route: Route): User = get(route, "auth/me", User.serializer())

    suspend fun homes(route: Route): List<Home> = get(route, "homes", ListSerializer(Home.serializer()))

    suspend fun overview(route: Route, homeId: String): Overview = get(route, "homes/$homeId/overview", Overview.serializer())

    suspend fun zones(route: Route, homeId: String): List<Zone> = get(route, "homes/$homeId/zones", ListSerializer(Zone.serializer()))

    suspend fun zone(route: Route, zoneId: String): Zone = get(route, "zones/$zoneId", Zone.serializer())

    suspend fun command(route: Route, zoneId: String, command: ZoneCommand): CommandAccepted =
        post(route, "zones/$zoneId/command", ShcJson.encodeToString(ZoneCommand.serializer(), command), CommandAccepted.serializer())

    suspend fun resume(route: Route, zoneId: String): Zone = post(route, "zones/$zoneId/resume", "{}", Zone.serializer())

    suspend fun lights(route: Route, homeId: String): LightsResponse = get(route, "homes/$homeId/lights", LightsResponse.serializer())

    suspend fun setGroup(route: Route, groupId: String, command: GroupCommand): CommandAccepted =
        post(route, "lights/groups/$groupId", ShcJson.encodeToString(GroupCommand.serializer(), command), CommandAccepted.serializer())

    suspend fun energySummary(route: Route, homeId: String, period: String = "day", date: String? = null): EnergySummary {
        val q = buildString {
            append("homes/$homeId/energy/summary?period=$period")
            if (date != null) append("&date=$date")
        }
        return get(route, q, EnergySummary.serializer())
    }

    suspend fun energyLive(route: Route, homeId: String): LivePower = get(route, "homes/$homeId/energy/live", LivePower.serializer())

    suspend fun alerts(route: Route, homeId: String): List<Alert> = get(route, "homes/$homeId/alerts", ListSerializer(Alert.serializer()))

    suspend fun ack(route: Route, alertId: String) {
        post<Unit>(route, "alerts/$alertId/ack", "{}", null)
    }

    suspend fun settings(route: Route): SystemSettings = get(route, "system/settings", SystemSettings.serializer())

    suspend fun clientSelf(route: Route): ClientView = get(route, "clients/self", ClientView.serializer())

    /** New token; the old one stops working immediately, so store the new one before using it. */
    suspend fun refreshToken(route: Route): TokenRefresh = post(route, "clients/self/refresh", "{}", TokenRefresh.serializer())

    /** "Ontkoppelen": revokes this phone's token on the server. */
    suspend fun unpairSelf(route: Route) {
        call<Unit>(route, "DELETE", "clients/self", null, null)
    }

    // ---- Plumbing ----

    private suspend fun <T> get(route: Route, path: String, ser: KSerializer<T>): T =
        call(route, "GET", path, null, ser)

    private suspend fun <T> post(route: Route, path: String, body: String, ser: KSerializer<T>?): T =
        call(route, "POST", path, body, ser)

    /** Builds the request with exactly the headers this route may get. Visible for tests. */
    fun request(route: Route, method: String, path: String, body: String?, auth: Boolean): Request {
        val creds = credentials()
        val builder = Request.Builder()
            .url(UrlTools.api(route.baseUrl, path))
            .header("Accept", "application/json")
        if (auth) {
            if (!route.verified) {
                throw ApiException(0, "unverified_route", "Dit adres is niet als jouw server herkend; de app stuurt er geen sleutel heen.")
            }
            if (route.baseUrl.startsWith("http://") && !cleartextAllowed(route.baseUrl)) {
                throw ApiException(0, "cleartext_refused", "Onversleuteld verbinden kan alleen met een adres in je thuisnetwerk.")
            }
            creds.token?.let { builder.header("Authorization", "Bearer $it") }
        }
        if (route.kind == RouteKind.REMOTE && creds.hasCfAccess) {
            builder.header("CF-Access-Client-Id", creds.cfAccessClientId!!)
            builder.header("CF-Access-Client-Secret", creds.cfAccessClientSecret!!)
        }
        when (method) {
            "GET" -> builder.get()
            "DELETE" -> if (body == null) builder.delete() else builder.delete(body.toRequestBody(jsonType))
            else -> builder.method(method, (body ?: "").toRequestBody(jsonType))
        }
        return builder.build()
    }

    @Suppress("UNCHECKED_CAST")
    private suspend fun <T> call(
        route: Route,
        method: String,
        path: String,
        body: String?,
        ser: KSerializer<T>?,
        auth: Boolean = true,
        client: OkHttpClient = http,
    ): T {
        val response = client.newCall(request(route, method, path, body, auth)).await()
        response.use { r ->
            val text = r.body?.string().orEmpty()
            if (!r.isSuccessful) throw parseError(r.code, text, r.header("Location"))
            if (ser == null) return Unit as T
            return try {
                ShcJson.decodeFromString(ser, text)
            } catch (e: IllegalArgumentException) {
                if (looksLikeJson(text)) {
                    throw ApiException(r.code, "bad_response", "Onverwacht antwoord van de server.", fromApi = true)
                }
                // e.g. a wifi login page answering 200 with HTML: not our API.
                throw ApiException(r.code, "not_api", "Dit adres geeft geen antwoord van je huis.")
            }
        }
    }

    companion object {
        private fun looksLikeJson(text: String): Boolean {
            val t = text.trimStart()
            return t.startsWith("{") || t.startsWith("[")
        }

        fun parseError(status: Int, text: String, location: String? = null): ApiException {
            if (looksLikeJson(text)) {
                try {
                    val detail = ShcJson.decodeFromString(ErrorBody.serializer(), text).detail
                    when (detail) {
                        is JsonObject -> {
                            val code = (detail["code"] as? JsonPrimitive)?.contentOrNull ?: "http_$status"
                            val msg = (detail["message"] as? JsonPrimitive)?.contentOrNull?.takeIf { it.isNotBlank() }
                                ?: fallbackMessage(status)
                            return ApiException(status, code, msg, fromApi = true)
                        }
                        is JsonPrimitive -> return ApiException(status, "http_$status", detail.contentOrNull ?: fallbackMessage(status), fromApi = true)
                        else -> Unit
                    }
                } catch (e: IllegalArgumentException) {
                    // fall through: not the API's error format
                }
            }
            return when {
                status in 300..399 && location?.contains("cloudflareaccess.com") == true -> ApiException(
                    status,
                    "cf_access_login",
                    "Cloudflare Access laat de app niet door. Vul de service token in onder Meer › Cloudflare Access.",
                )
                status in 300..399 -> ApiException(status, "redirect", "Dit adres stuurt door naar een andere pagina; het lijkt niet je huis.")
                status == 530 || status in 520..529 -> ApiException(status, "tunnel_unavailable", "De tunnel naar je huis werkt nu niet.")
                status in 500..599 -> ApiException(status, "server_unavailable", "Je huis is nu niet bereikbaar.")
                status == 403 -> ApiException(status, "http_403", "Toegang geweigerd onderweg (Cloudflare). Controleer Meer › Cloudflare Access.")
                else -> ApiException(status, "http_$status", fallbackMessage(status))
            }
        }

        private fun fallbackMessage(status: Int): String = when (status) {
            401 -> "Opnieuw koppelen nodig."
            403 -> "Je hebt hier geen rechten voor."
            404 -> "Niet gevonden."
            409 -> "Dit kan nu niet."
            413 -> "Het verzoek is te groot."
            429 -> "Te veel verzoeken, probeer het zo opnieuw."
            503 -> "Je huis is nu niet bereikbaar."
            else -> "Serverfout ($status)."
        }
    }
}

/** Suspends until the OkHttp call completes; cancels the call with the coroutine. */
suspend fun Call.await(): Response = suspendCancellableCoroutine { cont ->
    enqueue(object : Callback {
        override fun onResponse(call: Call, response: Response) {
            cont.resume(response) { _, _, _ -> response.close() }
        }

        override fun onFailure(call: Call, e: IOException) {
            if (!cont.isCancelled) cont.resumeWithException(e)
        }
    })
    cont.invokeOnCancellation { runCatching { cancel() } }
}
