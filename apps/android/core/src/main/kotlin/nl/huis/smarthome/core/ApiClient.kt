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

/** Credentials sent with every request. Cloudflare Access headers are optional. */
data class Credentials(
    val token: String? = null,
    val cfAccessClientId: String? = null,
    val cfAccessClientSecret: String? = null,
)

class ApiException(val status: Int, val code: String, override val message: String) : IOException(message) {
    val isAuthError: Boolean get() = status == 401 || code == "auth_required"
}

/**
 * Small typed client for the Smart Home Control API (docs/api.md).
 * [baseUrl] is resolved per call so the route (LAN / tunnel) can change at runtime.
 */
class ApiClient(
    private val http: OkHttpClient,
    private val baseUrl: () -> String?,
    private val credentials: () -> Credentials,
) {
    private val jsonType = "application/json; charset=utf-8".toMediaType()

    // ---- Pairing & health (explicit base URL, before a route is known) ----

    suspend fun redeem(base: String, code: String, deviceName: String): RedeemResponse =
        call(base, "POST", "pairing/redeem", ShcJson.encodeToString(RedeemRequest.serializer(), RedeemRequest(code, deviceName)), RedeemResponse.serializer(), auth = false)

    suspend fun health(base: String, client: OkHttpClient = http): Health =
        call(base, "GET", "health", null, Health.serializer(), auth = false, client = client)

    // ---- Regular endpoints ----

    suspend fun me(): User = get("auth/me", User.serializer())

    suspend fun homes(): List<Home> = get("homes", ListSerializer(Home.serializer()))

    suspend fun overview(homeId: String): Overview = get("homes/$homeId/overview", Overview.serializer())

    suspend fun zones(homeId: String): List<Zone> = get("homes/$homeId/zones", ListSerializer(Zone.serializer()))

    suspend fun zone(zoneId: String): Zone = get("zones/$zoneId", Zone.serializer())

    suspend fun command(zoneId: String, command: ZoneCommand): CommandAccepted =
        post("zones/$zoneId/command", ShcJson.encodeToString(ZoneCommand.serializer(), command), CommandAccepted.serializer())

    suspend fun resume(zoneId: String): Zone = post("zones/$zoneId/resume", "{}", Zone.serializer())

    suspend fun lights(homeId: String): LightsResponse = get("homes/$homeId/lights", LightsResponse.serializer())

    suspend fun setGroup(groupId: String, command: GroupCommand) {
        post<Unit>("lights/groups/$groupId", ShcJson.encodeToString(GroupCommand.serializer(), command), null)
    }

    suspend fun energySummary(homeId: String, period: String = "day", date: String? = null): EnergySummary {
        val q = buildString {
            append("homes/$homeId/energy/summary?period=$period")
            if (date != null) append("&date=$date")
        }
        return get(q, EnergySummary.serializer())
    }

    suspend fun energyLive(homeId: String): LivePower = get("homes/$homeId/energy/live", LivePower.serializer())

    suspend fun alerts(homeId: String): List<Alert> = get("homes/$homeId/alerts", ListSerializer(Alert.serializer()))

    suspend fun ack(alertId: String) {
        post<Unit>("alerts/$alertId/ack", "{}", null)
    }

    // ---- Plumbing ----

    private suspend fun <T> get(path: String, ser: KSerializer<T>): T =
        call(requireBase(), "GET", path, null, ser)

    private suspend fun <T> post(path: String, body: String, ser: KSerializer<T>?): T =
        call(requireBase(), "POST", path, body, ser)

    private fun requireBase(): String =
        baseUrl() ?: throw ApiException(0, "no_route", "Geen verbinding met je huis.")

    @Suppress("UNCHECKED_CAST")
    private suspend fun <T> call(
        base: String,
        method: String,
        path: String,
        body: String?,
        ser: KSerializer<T>?,
        auth: Boolean = true,
        client: OkHttpClient = http,
    ): T {
        val creds = credentials()
        val builder = Request.Builder()
            .url(UrlTools.api(base, path))
            .header("Accept", "application/json")
        if (auth && creds.token != null) builder.header("Authorization", "Bearer ${creds.token}")
        if (!creds.cfAccessClientId.isNullOrBlank() && !creds.cfAccessClientSecret.isNullOrBlank()) {
            builder.header("CF-Access-Client-Id", creds.cfAccessClientId)
            builder.header("CF-Access-Client-Secret", creds.cfAccessClientSecret)
        }
        when (method) {
            "GET" -> builder.get()
            else -> builder.method(method, (body ?: "").toRequestBody(jsonType))
        }
        val response = client.newCall(builder.build()).await()
        response.use { r ->
            val text = r.body?.string().orEmpty()
            if (!r.isSuccessful) throw parseError(r.code, text)
            if (ser == null) return Unit as T
            return try {
                ShcJson.decodeFromString(ser, text)
            } catch (e: IllegalArgumentException) {
                throw ApiException(r.code, "bad_response", "Onverwacht antwoord van de server.")
            }
        }
    }

    companion object {
        fun parseError(status: Int, text: String): ApiException {
            val fallback = when (status) {
                401 -> "Opnieuw koppelen nodig."
                403 -> "Je hebt hier geen rechten voor."
                404 -> "Niet gevonden."
                409 -> "Dit kan nu niet."
                429 -> "Te veel verzoeken, probeer het zo opnieuw."
                503 -> "Apparaat niet bereikbaar."
                else -> "Serverfout ($status)."
            }
            val default = ApiException(status, "http_$status", fallback)
            if (text.isBlank()) return default
            return try {
                val detail = ShcJson.decodeFromString(ErrorBody.serializer(), text).detail ?: return default
                when (detail) {
                    is JsonObject -> {
                        val code = (detail["code"] as? JsonPrimitive)?.contentOrNull ?: default.code
                        val msg = (detail["message"] as? JsonPrimitive)?.contentOrNull?.takeIf { it.isNotBlank() } ?: fallback
                        ApiException(status, code, msg)
                    }
                    is JsonPrimitive -> ApiException(status, default.code, detail.contentOrNull ?: fallback)
                    else -> default
                }
            } catch (e: IllegalArgumentException) {
                default
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
