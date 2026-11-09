package nl.huis.smarthome.core

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonElement

/** Shared JSON configuration: tolerant of new server fields. */
val ShcJson: Json = Json {
    ignoreUnknownKeys = true
    explicitNulls = false
    coerceInputValues = true
    encodeDefaults = false
}

/**
 * Logged-in user as returned by `GET /auth/me` (and inside redeem/hello).
 * [canControl] and [isAdminFlag] combine the user role with the phone's own role
 * (admin/control/viewer); they are only present in `/auth/me` and the WebSocket hello.
 */
@Serializable
data class User(
    val id: String = "",
    val username: String = "",
    val role: String = "viewer",
    val via: String? = null,
    @SerialName("client_role") val clientRole: String? = null,
    @SerialName("is_admin") val isAdminFlag: Boolean? = null,
    @SerialName("can_control") val canControl: Boolean? = null,
) {
    val isAdmin: Boolean get() = isAdminFlag ?: (role == "admin")
}

@Serializable
data class Home(
    val id: String,
    val name: String,
    val timezone: String = "Europe/Amsterdam",
    val currency: String = "EUR",
    val active: Boolean = true,
)

/** One line of `Overview.status` / `status.updated` (worker, safe mode, backup, integrations). */
@Serializable
data class StatusItem(
    val key: String,
    val label: String = "",
    val state: String = "unknown",
    val detail: String? = null,
    val code: String? = null,
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
data class BlockedMode(val mode: String, val reason: String = "")

@Serializable
data class NextChange(
    val at: String,
    @SerialName("target_c") val targetC: Double? = null,
    /** Mode the block switches to; null = keeps the current mode. */
    val mode: String? = null,
    val label: String? = null,
)

@Serializable
data class ZonePolicy(
    @SerialName("band_c") val bandC: Double? = null,
    @SerialName("min_interval_s") val minIntervalS: Int? = null,
    @SerialName("max_step_c") val maxStepC: Double? = null,
    @SerialName("max_starts_per_hour") val maxStartsPerHour: Int? = null,
    @SerialName("stale_after_s") val staleAfterS: Int? = null,
    @SerialName("plausible_min_c") val plausibleMinC: Double? = null,
    @SerialName("plausible_max_c") val plausibleMaxC: Double? = null,
    @SerialName("max_offset_c") val maxOffsetC: Double? = null,
    @SerialName("override_default_minutes") val overrideDefaultMinutes: Int? = null,
    @SerialName("energy_priority") val energyPriority: String? = null,
    @SerialName("watchdog_minutes") val watchdogMinutes: Int? = null,
    @SerialName("watchdog_min_progress_c") val watchdogMinProgressC: Double? = null,
)

/** The command in flight for a zone (`Zone.pending_command`). */
@Serializable
data class PendingCommandRef(val id: String, val status: String = "queued", val since: String? = null)

/** Whether a zone has an airco and whether it answers (verbeterlijst 31). */
enum class UnitLink { NOT_LINKED, OFFLINE, ONLINE }

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
    @SerialName("profile_id") val profileId: String? = null,
    val override: Override? = null,
    val capabilities: ZoneCapabilities = ZoneCapabilities(),
    @SerialName("blocked_modes") val blockedModes: List<BlockedMode> = emptyList(),
    @SerialName("next_change") val nextChange: NextChange? = null,
    val policy: ZonePolicy = ZonePolicy(),
    val notice: String? = null,
    /** "info" (neutral) or "warn"; null on servers before this field existed. */
    @SerialName("notice_level") val noticeLevel: String? = null,
    @SerialName("unit_device_id") val unitDeviceId: String? = null,
    @SerialName("sensor_device_id") val sensorDeviceId: String? = null,
    @SerialName("unit_linked") val unitLinked: Boolean = true,
    /** "not_linked" | "offline" | "online". */
    @SerialName("unit_status") val unitStatus: String? = null,
    @SerialName("can_resume") val canResume: Boolean? = null,
    @SerialName("setpoint_adjustable") val setpointAdjustable: Boolean? = null,
    @SerialName("pending_command") val pendingCommand: PendingCommandRef? = null,
    @SerialName("correction_c") val correctionC: Double? = null,
    @SerialName("energy_priority") val energyPriority: String? = null,
    @SerialName("last_decision") val lastDecision: String? = null,
    @SerialName("watchdog_since") val watchdogSince: String? = null,
) {
    fun blockReason(mode: String): String? = blockedModes.firstOrNull { it.mode == mode }?.reason

    /** Not linked (neutral text, never red), offline (warning) or online. */
    val unitLink: UnitLink
        get() = when (unitStatus) {
            "not_linked" -> UnitLink.NOT_LINKED
            "offline" -> UnitLink.OFFLINE
            "online" -> UnitLink.ONLINE
            else -> if (!unitLinked) UnitLink.NOT_LINKED else if (online) UnitLink.ONLINE else UnitLink.OFFLINE
        }

    /** "Terug naar schema" is offered (also when the unit was switched off). */
    val resumable: Boolean
        get() = canResume ?: (unitLinked && (control == "manual" || control == "off" || override != null))

    /** +/−, fan and "Handmatig 2 uur" are possible; false while the unit is offline or off. */
    val adjustable: Boolean
        get() = setpointAdjustable ?: (unitLinked && online && mode != "off")

    /** The notice needs attention (warning colour) rather than a neutral info line. */
    val noticeIsWarning: Boolean
        get() = notice != null && (noticeLevel == "warn" || (noticeLevel == null && unitLink != UnitLink.NOT_LINKED))
}

@Serializable
data class LastCommand(
    val id: String,
    val action: String? = null,
    val status: String = "queued",
    val detail: String = "",
    val at: String? = null,
)

@Serializable
data class LightGroup(
    val id: String,
    val name: String,
    val on: Boolean = false,
    val brightness: Int? = null,
    val scene: String? = null,
    @SerialName("scene_id") val sceneId: String? = null,
    val reachable: Boolean = true,
    @SerialName("lights_on") val lightsOn: Int = 0,
    @SerialName("lights_total") val lightsTotal: Int = 0,
    @SerialName("lights_reachable") val lightsReachable: Int? = null,
    @SerialName("owner_type") val ownerType: String? = null,
    /** The bridge's "Alle lampen" group: show it last or hide it. */
    @SerialName("all_lights") val allLights: Boolean = false,
    val hidden: Boolean = false,
    @SerialName("last_command") val lastCommand: LastCommand? = null,
)

@Serializable
data class Scene(
    val id: String,
    val name: String,
    @SerialName("group_id") val groupId: String? = null,
    val active: Boolean = false,
)

@Serializable
data class Light(
    val id: String,
    val name: String,
    val on: Boolean = false,
    val brightness: Int? = null,
    val reachable: Boolean = true,
    val model: String? = null,
    @SerialName("last_command") val lastCommand: LastCommand? = null,
)

@Serializable
data class LightsResponse(
    val groups: List<LightGroup> = emptyList(),
    val scenes: List<Scene> = emptyList(),
    val lights: List<Light> = emptyList(),
)

@Serializable
data class MeterSummary(
    val role: String,
    val label: String = "",
    val kwh: Double? = null,
    val unit: String = "kWh",
    val cost: Double? = null,
    @SerialName("cost_coverage") val costCoverage: Double? = null,
    val quality: Quality = Quality.MISSING,
    val note: String? = null,
    val notes: List<String> = emptyList(),
)

@Serializable
data class EnergyBucket(
    val start: String,
    val end: String? = null,
    val values: Map<String, Double?> = emptyMap(),
    val complete: Boolean = true,
    val cost: Double? = null,
    val notes: List<String> = emptyList(),
    @SerialName("gas_m3") val gasM3: Double? = null,
)

@Serializable
data class GasSummary(
    val m3: Double? = null,
    val unit: String = "m³",
    val cost: Double? = null,
    @SerialName("cost_coverage") val costCoverage: Double? = null,
    val quality: Quality = Quality.MISSING,
    val note: String? = null,
)

@Serializable
data class EnergySummary(
    val period: String = "day",
    val start: String? = null,
    val end: String? = null,
    val timezone: String? = null,
    @SerialName("total_kwh") val totalKwh: Double? = null,
    val cost: Double? = null,
    /** Share (0..1) of grid kWh that has a tariff. */
    @SerialName("cost_coverage") val costCoverage: Double? = null,
    @SerialName("fixed_cost") val fixedCost: Double? = null,
    @SerialName("fixed_days") val fixedDays: Int? = null,
    @SerialName("cost_note") val costNote: String? = null,
    val currency: String = "EUR",
    val meters: List<MeterSummary> = emptyList(),
    val gas: GasSummary? = null,
    val buckets: List<EnergyBucket> = emptyList(),
)

@Serializable
data class PowerPart(
    val role: String,
    val label: String = "",
    val watts: Double? = null,
    val quality: Quality = Quality.MISSING,
    val at: String? = null,
    /** A sub-meter without a recent reading: shown as missing instead of being dropped. */
    val stale: Boolean = false,
    val note: String? = null,
)

@Serializable
data class LivePower(
    /** Time of the P1 reading. */
    val at: String? = null,
    @SerialName("total_w") val totalW: Double? = null,
    val complete: Boolean = true,
    val note: String? = null,
    val parts: List<PowerPart> = emptyList(),
)

@Serializable
data class Alert(
    val id: String,
    val severity: String = "info",
    val title: String = "",
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
    @SerialName("server_id") val serverId: String? = null,
    @SerialName("lan_url") val lanUrl: String? = null,
    @SerialName("remote_url") val remoteUrl: String? = null,
    @SerialName("home_id") val homeId: String? = null,
    @SerialName("home_name") val homeName: String? = null,
)

/** A paired phone as the server sees it (`GET /clients/self`, `client` in the redeem answer). */
@Serializable
data class ClientView(
    val id: String = "",
    val name: String? = null,
    /** "admin" | "control" | "viewer". */
    val role: String? = null,
    @SerialName("created_at") val createdAt: String? = null,
    @SerialName("last_used_at") val lastUsedAt: String? = null,
    @SerialName("expires_at") val expiresAt: String? = null,
    @SerialName("refreshed_at") val refreshedAt: String? = null,
    val revoked: Boolean = false,
    val expired: Boolean = false,
    val user: String? = null,
)

@Serializable
data class RedeemResponse(
    val token: String,
    val user: User,
    val client: ClientView? = null,
    val server: ServerInfo = ServerInfo(),
)

/** `POST /clients/self/refresh`: the old token stops working immediately. */
@Serializable
data class TokenRefresh(val token: String, @SerialName("expires_at") val expiresAt: String? = null)

/** `GET /pairing/identity` (no login). */
@Serializable
data class ServerIdentity(@SerialName("server_id") val serverId: String? = null, val name: String? = null)

/** `GET /system/settings`. */
@Serializable
data class SystemSettings(
    @SerialName("lan_url") val lanUrl: String? = null,
    @SerialName("remote_url") val remoteUrl: String? = null,
    @SerialName("safe_mode") val safeMode: Boolean = false,
    val maintenance: Boolean = false,
)

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

/** Realtime `command.updated`. */
@Serializable
data class CommandUpdate(
    val id: String,
    val status: String,
    val detail: String? = null,
    val attempts: Int? = null,
    val retrying: Boolean = false,
)

/** Realtime `hello` after a successful WebSocket login. */
@Serializable
data class Hello(
    val user: User? = null,
    val via: String? = null,
    @SerialName("client_role") val clientRole: String? = null,
    @SerialName("can_control") val canControl: Boolean? = null,
)

/** Realtime `error` sent right before the server closes the socket (4401 / 4403). */
@Serializable
data class WsError(val code: String = "", val reason: String? = null)

@Serializable
data class ErrorDetail(val code: String = "error", val message: String = "")

@Serializable
data class ErrorBody(val detail: JsonElement? = null)

/** Raw realtime frame: `{"type": "...", "data": ...}`; data may be an object or a list. */
@Serializable
data class WsFrame(val type: String, val data: JsonElement? = null)
