package nl.huis.smarthome.core

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonObject

/** Shared JSON configuration: tolerant of new server fields. */
val ShcJson: Json = Json {
    ignoreUnknownKeys = true
    explicitNulls = false
    coerceInputValues = true
    encodeDefaults = false
}

@Serializable
data class User(val id: String, val username: String, val role: String = "viewer") {
    val isAdmin: Boolean get() = role == "admin"
}

@Serializable
data class Home(
    val id: String,
    val name: String,
    val timezone: String = "Europe/Amsterdam",
    val currency: String = "EUR",
    val active: Boolean = true,
)

@Serializable
data class StatusItem(
    val key: String,
    val label: String,
    val state: String = "unknown",
    val detail: String? = null,
)

@Serializable
enum class Quality {
    @SerialName("measured") MEASURED,
    @SerialName("derived") DERIVED,
    @SerialName("estimated") ESTIMATED,
    @SerialName("missing") MISSING,
}

@Serializable
data class Measurement(
    val value: Double? = null,
    val unit: String = "",
    val quality: Quality = Quality.MISSING,
    val at: String? = null,
    val stale: Boolean = false,
)

@Serializable
data class Override(val until: String? = null, val source: String = "app")

@Serializable
data class ZoneCapabilities(
    val modes: List<String> = emptyList(),
    val fan: Boolean = false,
    val swing: Boolean = false,
    @SerialName("min_c") val minC: Double = 16.0,
    @SerialName("max_c") val maxC: Double = 30.0,
    @SerialName("step_c") val stepC: Double = 0.5,
)

@Serializable
data class BlockedMode(val mode: String, val reason: String)

@Serializable
data class NextChange(
    val at: String,
    @SerialName("target_c") val targetC: Double? = null,
    val label: String? = null,
)

@Serializable
data class ZonePolicy(
    @SerialName("band_c") val bandC: Double? = null,
    @SerialName("min_interval_s") val minIntervalS: Int? = null,
    @SerialName("max_step_c") val maxStepC: Double? = null,
    @SerialName("max_starts_per_hour") val maxStartsPerHour: Int? = null,
    @SerialName("stale_after_s") val staleAfterS: Int? = null,
)

@Serializable
data class Zone(
    val id: String,
    val name: String,
    val room: String? = null,
    val online: Boolean = false,
    val mode: String = "off",
    @SerialName("target_c") val targetC: Double? = null,
    val fan: String? = null,
    val temperature: Measurement = Measurement(),
    val humidity: Measurement = Measurement(),
    val control: String = "off",
    val profile: String? = null,
    val override: Override? = null,
    val capabilities: ZoneCapabilities = ZoneCapabilities(),
    @SerialName("blocked_modes") val blockedModes: List<BlockedMode> = emptyList(),
    @SerialName("next_change") val nextChange: NextChange? = null,
    val policy: ZonePolicy = ZonePolicy(),
    val notice: String? = null,
) {
    fun blockReason(mode: String): String? = blockedModes.firstOrNull { it.mode == mode }?.reason
}

@Serializable
data class LightGroup(
    val id: String,
    val name: String,
    val on: Boolean = false,
    val brightness: Int? = null,
    val scene: String? = null,
    val reachable: Boolean = true,
    @SerialName("lights_on") val lightsOn: Int = 0,
    @SerialName("lights_total") val lightsTotal: Int = 0,
)

@Serializable
data class Scene(val id: String, val name: String, @SerialName("group_id") val groupId: String? = null)

@Serializable
data class LightsResponse(val groups: List<LightGroup> = emptyList(), val scenes: List<Scene> = emptyList())

@Serializable
data class MeterSummary(
    val role: String,
    val label: String,
    val kwh: Double? = null,
    val cost: Double? = null,
    val quality: Quality = Quality.MISSING,
    val note: String? = null,
)

@Serializable
data class EnergyBucket(
    val start: String,
    val values: Map<String, Double?> = emptyMap(),
    val complete: Boolean = true,
)

@Serializable
data class EnergySummary(
    val period: String = "day",
    val start: String? = null,
    val end: String? = null,
    @SerialName("total_kwh") val totalKwh: Double? = null,
    val cost: Double? = null,
    val currency: String = "EUR",
    val meters: List<MeterSummary> = emptyList(),
    val buckets: List<EnergyBucket> = emptyList(),
)

@Serializable
data class PowerPart(
    val role: String,
    val label: String,
    val watts: Double? = null,
    val quality: Quality = Quality.MISSING,
)

@Serializable
data class LivePower(
    val at: String? = null,
    @SerialName("total_w") val totalW: Double? = null,
    val parts: List<PowerPart> = emptyList(),
)

@Serializable
data class Alert(
    val id: String,
    val severity: String = "info",
    val title: String,
    val detail: String? = null,
    @SerialName("created_at") val createdAt: String? = null,
    val acknowledged: Boolean = false,
    val source: String? = null,
)

@Serializable
data class Overview(
    val home: Home,
    val status: List<StatusItem> = emptyList(),
    val zones: List<Zone> = emptyList(),
    val lights: List<LightGroup> = emptyList(),
    @SerialName("energy_today") val energyToday: EnergySummary? = null,
    @SerialName("power_now") val powerNow: LivePower? = null,
    val alerts: List<Alert> = emptyList(),
)

// ---- Requests / responses ----

@Serializable
data class RedeemRequest(val code: String, @SerialName("device_name") val deviceName: String)

@Serializable
data class ServerInfo(
    @SerialName("lan_url") val lanUrl: String? = null,
    @SerialName("remote_url") val remoteUrl: String? = null,
    @SerialName("home_id") val homeId: String? = null,
    @SerialName("home_name") val homeName: String? = null,
)

@Serializable
data class RedeemResponse(val token: String, val user: User, val server: ServerInfo = ServerInfo())

@Serializable
data class ZoneCommand(
    val mode: String? = null,
    @SerialName("target_c") val targetC: Double? = null,
    val fan: String? = null,
    @SerialName("override_minutes") val overrideMinutes: Int? = null,
)

@Serializable
data class CommandAccepted(@SerialName("command_id") val commandId: String? = null, val status: String = "queued")

@Serializable
data class GroupCommand(
    val on: Boolean? = null,
    val brightness: Int? = null,
    @SerialName("scene_id") val sceneId: String? = null,
)

@Serializable
data class Health(val status: String = "", val version: String? = null)

@Serializable
data class ErrorDetail(val code: String = "error", val message: String = "")

@Serializable
data class ErrorBody(val detail: JsonElement? = null)

/** Raw realtime frame: `{"type": "...", "data": {...}}`. */
@Serializable
data class WsFrame(val type: String, val data: JsonObject? = null)
