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
import java.util.Locale
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
    REVOKED("De koppeling is ingetrokken. Koppel opnieuw met een QR-code uit de web-app (Account › Telefoons)."),
    EXPIRED("De koppeling is verlopen. Koppel opnieuw met een QR-code uit de web-app (Account › Telefoons)."),
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
 * A failure that the client describes itself, because the answer had no message of the server's
 * own (a Cloudflare or proxy page, a redirect, a refused route, a bare status code). The app shows
 * its strings.xml text for it (data/CoreTexts.kt), so TalkBack and translations read the same
 * resources; [dutch] is the same text for JVM callers and tests. [SERVER_ERROR] names the status.
 */
enum class ApiProblem(val dutch: String) {
    UNVERIFIED_ROUTE("Dit adres is niet als jouw server herkend; de app stuurt er geen sleutel heen."),
    CLEARTEXT_REFUSED("Onversleuteld verbinden kan alleen met een adres in je thuisnetwerk."),
    BAD_RESPONSE("Onverwacht antwoord van de server."),
    NOT_API("Dit adres geeft geen antwoord van je huis."),
    CF_ACCESS_LOGIN("Cloudflare Access laat de app niet door. Vul de service token in onder Meer › Cloudflare Access."),
    CF_FORBIDDEN("Toegang geweigerd onderweg (Cloudflare). Controleer Meer › Cloudflare Access."),
    REDIRECT("Dit adres stuurt door naar een andere pagina; het lijkt niet je huis."),
    TUNNEL_UNAVAILABLE("De tunnel naar je huis werkt nu niet."),
    SERVER_UNAVAILABLE("Je huis is nu niet bereikbaar."),
    REPAIR_NEEDED("Opnieuw koppelen nodig."),
    NO_RIGHTS("Je hebt hier geen rechten voor."),
    NOT_FOUND("Niet gevonden."),
    CONFLICT("Dit kan nu niet."),
    TOO_LARGE("Het verzoek is te groot."),
    TOO_MANY_REQUESTS("Te veel verzoeken, probeer het zo opnieuw."),
    SERVER_ERROR("Serverfout (%d).");

    /** The text needs the HTTP status (`%d` here, `%1$d` in strings.xml). */
    val withStatus: Boolean get() = this == SERVER_ERROR

    /** The Dutch text for an answer with [status]. */
    fun dutch(status: Int): String = if (withStatus) dutch.format(Locale.ROOT, status) else dutch

    companion object {
        /** The client's text for a bare [status] (no usable message in the answer). */
        fun forStatus(status: Int): ApiProblem = when (status) {
            401 -> REPAIR_NEEDED
            403 -> NO_RIGHTS
            404 -> NOT_FOUND
            409 -> CONFLICT
            413 -> TOO_LARGE
            429 -> TOO_MANY_REQUESTS
            503 -> SERVER_UNAVAILABLE
            else -> SERVER_ERROR
        }
    }
}

/**
 * An HTTP answer that was not a success. [fromApi] is true when the body was the API's own JSON
 * error (`{"detail": …}`); false for proxy, Cloudflare or captive-portal pages. [problem] is set
 * when [message] is the client's own text rather than the server's: the app then shows the
 * strings.xml text for that code instead of [message].
 */
class ApiException(
    val status: Int,
    val code: String,
    override val message: String,
    val fromApi: Boolean = false,
    val problem: ApiProblem? = null,
) : IOException(message) {
    /** A failure the client describes itself: [message] is [problem]'s Dutch text. */
    constructor(status: Int, code: String, problem: ApiProblem, fromApi: Boolean = false) :
        this(status, code, problem.dutch(status), fromApi, problem)

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

    /**
     * Who answers on [base]. With a [challenge] (a paired phone) the answer carries the server's
     * proof, checked with [IdentityProof.check]; the token itself is never sent here.
     */
    suspend fun identity(
        base: String,
        kind: RouteKind,
        client: OkHttpClient = http,
        challenge: IdentityChallenge? = null,
    ): ServerIdentity {
        val path = if (challenge == null) "pairing/identity" else "pairing/identity?${challenge.query}"
        return call(Route(kind, base), "GET", path, null, ServerIdentity.serializer(), auth = false, client = client)
    }

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

    /** The zone's week schedule (read-only in the app; edited in the web app). */
    suspend fun schedule(route: Route, zoneId: String): ZoneSchedule = get(route, "zones/$zoneId/schedule", ZoneSchedule.serializer())

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
                throw ApiException(0, "unverified_route", ApiProblem.UNVERIFIED_ROUTE)
            }
            if (route.baseUrl.startsWith("http://") && !cleartextAllowed(route.baseUrl)) {
                throw ApiException(0, "cleartext_refused", ApiProblem.CLEARTEXT_REFUSED)
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
                    throw ApiException(r.code, "bad_response", ApiProblem.BAD_RESPONSE, fromApi = true)
                }
                // e.g. a wifi login page answering 200 with HTML: not our API.
                throw ApiException(r.code, "not_api", ApiProblem.NOT_API)
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
                                ?: return ApiException(status, code, ApiProblem.forStatus(status), fromApi = true)
                            return ApiException(status, code, msg, fromApi = true)
                        }
                        is JsonPrimitive -> {
                            val msg = detail.contentOrNull?.takeIf { it.isNotBlank() }
                                ?: return ApiException(status, "http_$status", ApiProblem.forStatus(status), fromApi = true)
                            return ApiException(status, "http_$status", msg, fromApi = true)
                        }
                        else -> Unit
                    }
                } catch (e: IllegalArgumentException) {
                    // fall through: not the API's error format
                }
            }
            return when {
                status in 300..399 && location?.contains("cloudflareaccess.com") == true ->
                    ApiException(status, "cf_access_login", ApiProblem.CF_ACCESS_LOGIN)
                status in 300..399 -> ApiException(status, "redirect", ApiProblem.REDIRECT)
                status == 530 || status in 520..529 -> ApiException(status, "tunnel_unavailable", ApiProblem.TUNNEL_UNAVAILABLE)
                status in 500..599 -> ApiException(status, "server_unavailable", ApiProblem.SERVER_UNAVAILABLE)
                status == 403 -> ApiException(status, "http_403", ApiProblem.CF_FORBIDDEN)
                else -> ApiException(status, "http_$status", ApiProblem.forStatus(status))
            }
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
