package nl.huis.smarthome.core

import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import java.time.Duration
import java.time.Instant
import kotlin.math.abs

// ---------------------------------------------------------------------------
// Command tracking (verbeterlijst 23): a command is only "done" when the server says so
// through `command.updated` (or the zone shows the requested state), never on any zone update.
// ---------------------------------------------------------------------------

enum class CommandStatus {
    /** Accepted (202), waiting for the worker. */
    QUEUED,

    /** Failed once, the server tries again (same command id). */
    RETRYING,

    /** Sent to the device, not yet seen in a readback. */
    SENT,
    CONFIRMED,
    FAILED,

    /** Refused by the safety rules (e.g. heating and cooling on one outdoor unit). */
    DENIED,

    /** Dropped: a newer command or a manual/off zone replaced it. */
    CANCELLED,

    /** No outcome arrived in time. */
    TIMEOUT;

    val final: Boolean get() = this == CONFIRMED || this == FAILED || this == DENIED || this == CANCELLED || this == TIMEOUT
    val failed: Boolean get() = this == FAILED || this == DENIED || this == TIMEOUT

    companion object {
        fun fromWire(status: String?, retrying: Boolean = false): CommandStatus? = when (status) {
            "queued", "sending" -> if (retrying) RETRYING else QUEUED
            "sent" -> SENT
            "confirmed" -> CONFIRMED
            "failed" -> FAILED
            "denied" -> DENIED
            "cancelled" -> CANCELLED
            else -> null
        }
    }
}

enum class CommandTarget { ZONE, LIGHT_GROUP }

data class TrackedCommand(
    val id: String,
    /** Zone id or light-group id. */
    val targetId: String,
    val target: CommandTarget,
    val status: CommandStatus,
    val detail: String? = null,
    val attempts: Int? = null,
    /** What was asked, so the UI can keep showing it while the command is in flight. */
    val requested: ZoneCommand? = null,
    val requestedLight: GroupCommand? = null,
    val sentAt: Instant,
    val updatedAt: Instant,
) {
    val inFlight: Boolean get() = !status.final

    /** Short Dutch status line for the UI. */
    val label: String
        get() = when (status) {
            CommandStatus.QUEUED -> "Verzonden · wacht op bevestiging…"
            CommandStatus.RETRYING -> "Lukte niet, opnieuw proberen…"
            CommandStatus.SENT -> if (target == CommandTarget.LIGHT_GROUP) "Verzonden" else "Verzonden, nog niet bevestigd"
            CommandStatus.CONFIRMED -> "Bevestigd"
            CommandStatus.FAILED -> "Mislukt" + (detail?.takeIf { it.isNotBlank() }?.let { ": $it" } ?: "")
            CommandStatus.DENIED -> "Geweigerd" + (detail?.takeIf { it.isNotBlank() }?.let { ": $it" } ?: "")
            CommandStatus.CANCELLED -> "Vervangen door een nieuwere opdracht"
            CommandStatus.TIMEOUT -> "Geen bevestiging ontvangen. Controleer de stand."
        }
}

class CommandTracker(
    /** Climate: 3 attempts with readback timeouts of 2 min each can take this long. */
    private val zoneTimeout: Duration = Duration.ofMinutes(8),
    private val lightTimeout: Duration = Duration.ofSeconds(30),
    private val lingerOk: Duration = Duration.ofSeconds(4),
    private val lingerError: Duration = Duration.ofMinutes(2),
    /** A zone shown without our command but no final status yet: wait this long for command.updated. */
    private val settleGrace: Duration = Duration.ofSeconds(10),
) {
    private val byTarget = linkedMapOf<String, TrackedCommand>()
    private val settledAt = mutableMapOf<String, Instant>()

    @Synchronized
    fun accepted(
        id: String,
        targetId: String,
        target: CommandTarget,
        now: Instant,
        requested: ZoneCommand? = null,
        requestedLight: GroupCommand? = null,
    ): TrackedCommand {
        val c = TrackedCommand(id, targetId, target, CommandStatus.QUEUED, null, null, requested, requestedLight, now, now)
        byTarget[targetId] = c
        settledAt.remove(id)
        return c
    }

    /** `command.updated`. Returns the command when it changed (so the caller can report a failure). */
    @Synchronized
    fun onUpdate(update: CommandUpdate, now: Instant): TrackedCommand? {
        val current = byTarget.values.firstOrNull { it.id == update.id } ?: return null
        var status = CommandStatus.fromWire(update.status, update.retrying) ?: return null
        // A light command the bridge answered with errors counts as failed (same as the lights view).
        if (current.target == CommandTarget.LIGHT_GROUP && status == CommandStatus.SENT &&
            update.detail?.startsWith("Hue Bridge meldt") == true
        ) {
            status = CommandStatus.FAILED
        }
        val next = current.copy(
            status = status,
            detail = update.detail ?: current.detail,
            attempts = update.attempts ?: current.attempts,
            updatedAt = now,
        )
        if (next == current) return null
        byTarget[current.targetId] = next
        return next
    }

    /** A zone from the server (realtime or poll). Returns the command when it changed. */
    @Synchronized
    fun onZone(zone: Zone, now: Instant): TrackedCommand? {
        val current = byTarget[zone.id] ?: return null
        if (current.target != CommandTarget.ZONE || current.status.final) return null
        val pc = zone.pendingCommand
        val next = when {
            pc != null && pc.id == current.id -> {
                val s = CommandStatus.fromWire(pc.status) ?: return null
                // A retry shows up as "queued" again; keep the retrying flag from command.updated.
                val keep = if (s == CommandStatus.QUEUED && current.status == CommandStatus.RETRYING) current.status else s
                if (keep == current.status) return null
                current.copy(status = keep, updatedAt = now)
            }
            pc != null -> current.copy(status = CommandStatus.CANCELLED, updatedAt = now)
            matches(current.requested, zone) -> current.copy(status = CommandStatus.CONFIRMED, updatedAt = now)
            else -> {
                // Settled on the server, outcome not seen yet: command.updated normally follows at once.
                settledAt.putIfAbsent(current.id, now)
                return null
            }
        }
        byTarget[zone.id] = next
        return next
    }

    /**
     * Time-outs and clean-up; call every few seconds. Returns the commands that just timed out.
     */
    @Synchronized
    fun expire(now: Instant): List<TrackedCommand> {
        val timedOut = mutableListOf<TrackedCommand>()
        val it = byTarget.entries.iterator()
        while (it.hasNext()) {
            val e = it.next()
            val c = e.value
            if (c.inFlight) {
                val limit = if (c.target == CommandTarget.ZONE) zoneTimeout else lightTimeout
                val settled = settledAt[c.id]
                if (Duration.between(c.sentAt, now) > limit || (settled != null && Duration.between(settled, now) > settleGrace)) {
                    val t = c.copy(status = CommandStatus.TIMEOUT, updatedAt = now)
                    e.setValue(t)
                    settledAt.remove(c.id)
                    timedOut += t
                }
            } else {
                val linger = if (c.status.failed) lingerError else lingerOk
                if (Duration.between(c.updatedAt, now) > linger) {
                    it.remove()
                    settledAt.remove(c.id)
                }
            }
        }
        return timedOut
    }

    @Synchronized
    fun clear(targetId: String) {
        byTarget.remove(targetId)?.let { settledAt.remove(it.id) }
    }

    @Synchronized
    fun snapshot(): Map<String, TrackedCommand> = LinkedHashMap(byTarget)

    @Synchronized
    fun inFlightTargets(): Set<String> = byTarget.values.filter { it.inFlight }.map { it.targetId }.toSet()

    companion object {
        /** True when [zone] shows everything that [requested] asked for. */
        fun matches(requested: ZoneCommand?, zone: Zone): Boolean {
            if (requested == null) return false
            if (requested.mode != null && requested.mode != zone.mode) return false
            if (requested.targetC != null && requested.mode != "off") {
                val t = zone.targetC ?: return false
                if (abs(t - requested.targetC) > 0.01) return false
            }
            if (requested.fan != null && requested.fan != zone.fan) return false
            return true
        }
    }
}

/**
 * Waits until input stops for [delayMs] and then runs [action] once with the last value per key
 * (verbeterlijst 23: +/− sends one final setpoint). A running action is not cancelled by new input.
 */
class Debouncer<K, V>(
    private val scope: CoroutineScope,
    private val delayMs: Long = 800,
    private val action: suspend (K, V) -> Unit,
) {
    private val jobs = mutableMapOf<K, Job>()

    @Synchronized
    fun submit(key: K, value: V) {
        jobs.remove(key)?.cancel()
        jobs[key] = scope.launch {
            delay(delayMs)
            val self = coroutineContext[Job]
            synchronized(this@Debouncer) {
                if (jobs[key] === self) jobs.remove(key)
            }
            action(key, value)
        }
    }

    /** Drops a value that is still waiting. */
    @Synchronized
    fun cancel(key: K) {
        jobs.remove(key)?.cancel()
    }

    @Synchronized
    fun cancelAll() {
        jobs.values.forEach { it.cancel() }
        jobs.clear()
    }

    @Synchronized
    fun isWaiting(key: K): Boolean = jobs[key]?.isActive == true
}
