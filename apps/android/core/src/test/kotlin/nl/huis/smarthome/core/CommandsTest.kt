package nl.huis.smarthome.core

import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.test.advanceTimeBy
import kotlinx.coroutines.test.runCurrent
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.time.Instant

class CommandsTest {
    private val t0 = Instant.parse("2026-11-08T13:40:00Z")

    private fun zone(target: Double = 21.0, mode: String = "heat", pending: PendingCommandRef? = null) =
        Zone(id = "z1", name = "Woonkamer", mode = mode, targetC = target, pendingCommand = pending)

    @Test
    fun aZoneUpdateWithOldValuesIsNotAConfirmation() {
        val tr = CommandTracker()
        tr.accepted("c1", "z1", CommandTarget.ZONE, t0, requested = ZoneCommand(targetC = 21.5))
        // The server publishes the zone right after the command, still with the old target.
        assertNull(tr.onZone(zone(21.0, pending = PendingCommandRef("c1", "queued")), t0.plusMillis(50)))
        assertTrue(tr.snapshot()["z1"]!!.inFlight)
        assertEquals(setOf("z1"), tr.inFlightTargets())
        // Only command.updated "confirmed" finishes it.
        val done = tr.onUpdate(CommandUpdate("c1", "confirmed", "Bevestigd"), t0.plusSeconds(3))!!
        assertEquals(CommandStatus.CONFIRMED, done.status)
        assertFalse(done.inFlight)
        assertTrue(tr.inFlightTargets().isEmpty())
    }

    @Test
    fun followsRetriesSentAndFailures() {
        val tr = CommandTracker()
        tr.accepted("c1", "z1", CommandTarget.ZONE, t0, requested = ZoneCommand(mode = "heat"))
        val retry = tr.onUpdate(CommandUpdate("c1", "queued", "Time-out — opnieuw proberen (poging 2 van 3)", attempts = 1, retrying = true), t0.plusSeconds(5))!!
        assertEquals(CommandStatus.RETRYING, retry.status)
        assertEquals("Lukte niet, opnieuw proberen…", retry.label)
        assertEquals(CommandLabel.RETRYING, retry.labelCode)
        assertNull(retry.reason) // the retry detail is not part of the line
        // The poll shows the same command as queued: keep "retrying".
        assertNull(tr.onZone(zone(pending = PendingCommandRef("c1", "queued")), t0.plusSeconds(6)))
        val sent = tr.onUpdate(CommandUpdate("c1", "sent", "Niet bevestigd"), t0.plusSeconds(20))!!
        assertEquals(CommandStatus.SENT, sent.status)
        assertEquals(CommandLabel.SENT_UNCONFIRMED, sent.labelCode)
        assertNull(sent.reason)
        assertTrue(sent.inFlight) // a climate "sent" can still be confirmed or fail later
        val failed = tr.onUpdate(CommandUpdate("c1", "failed", "De unit liet de gevraagde stand niet zien"), t0.plusSeconds(140))!!
        assertTrue(failed.status.failed)
        assertEquals("Mislukt: De unit liet de gevraagde stand niet zien", failed.label)
        assertEquals(CommandLabel.FAILED, failed.labelCode)
        assertEquals("De unit liet de gevraagde stand niet zien", failed.reason)
        // Unknown ids are ignored.
        assertNull(tr.onUpdate(CommandUpdate("other", "confirmed"), t0))
    }

    @Test
    fun deniedAndCancelled() {
        val tr = CommandTracker()
        tr.accepted("c1", "z1", CommandTarget.ZONE, t0, requested = ZoneCommand(mode = "cool"))
        val d = tr.onUpdate(CommandUpdate("c1", "denied", "De slaapkamer verwarmt"), t0.plusSeconds(1))!!
        assertEquals("Geweigerd: De slaapkamer verwarmt", d.label)
        assertEquals(CommandLabel.DENIED, d.labelCode)
        assertEquals("De slaapkamer verwarmt", d.reason)

        tr.accepted("c2", "z1", CommandTarget.ZONE, t0, requested = ZoneCommand(targetC = 22.0))
        // Another command (other phone, a button) is now pending for the zone: ours was replaced.
        val c = tr.onZone(zone(pending = PendingCommandRef("c3", "queued")), t0.plusSeconds(2))!!
        assertEquals(CommandStatus.CANCELLED, c.status)
    }

    @Test
    fun pollWithoutRealtimeInfersOutcome() {
        val tr = CommandTracker()
        tr.accepted("c1", "z1", CommandTarget.ZONE, t0, requested = ZoneCommand(targetC = 21.5))
        // Settled on the server and the zone shows the requested target: confirmed.
        assertEquals(CommandStatus.CONFIRMED, tr.onZone(zone(21.5), t0.plusSeconds(20))!!.status)

        tr.accepted("c2", "z1", CommandTarget.ZONE, t0, requested = ZoneCommand(targetC = 23.0))
        assertNull(tr.onZone(zone(21.5), t0.plusSeconds(20)))
        assertTrue(tr.expire(t0.plusSeconds(25)).isEmpty()) // waiting for command.updated
        val timedOut = tr.expire(t0.plusSeconds(31))
        assertEquals(CommandStatus.TIMEOUT, timedOut.single().status)
    }

    @Test
    fun timeoutsAndCleanup() {
        val tr = CommandTracker()
        tr.accepted("l1", "g1", CommandTarget.LIGHT_GROUP, t0, requestedLight = GroupCommand(on = true))
        assertTrue(tr.expire(t0.plusSeconds(10)).isEmpty())
        assertEquals(CommandStatus.TIMEOUT, tr.expire(t0.plusSeconds(31)).single().status)
        // Errors stay visible a while, then disappear.
        assertTrue(tr.snapshot().containsKey("g1"))
        tr.expire(t0.plusSeconds(31 + 121))
        assertFalse(tr.snapshot().containsKey("g1"))

        tr.accepted("c1", "z1", CommandTarget.ZONE, t0)
        assertTrue(tr.expire(t0.plusSeconds(7 * 60)).isEmpty())
        assertEquals(1, tr.expire(t0.plusSeconds(8 * 60 + 1)).size)
    }

    @Test
    fun everyStatusHasAStatusLineCode() {
        val codes = CommandStatus.entries.flatMap { status ->
            CommandTarget.entries.map { target ->
                TrackedCommand("c1", "t1", target, status, detail = "reden", sentAt = t0, updatedAt = t0).labelCode
            }
        }.toSet()
        assertEquals(CommandLabel.entries.toSet(), codes)
        // Only a failure or refusal carries the server's reason.
        val reasons = CommandStatus.entries.filter {
            TrackedCommand("c1", "t1", CommandTarget.ZONE, it, detail = "reden", sentAt = t0, updatedAt = t0).reason != null
        }
        assertEquals(listOf(CommandStatus.FAILED, CommandStatus.DENIED), reasons)
        assertNull(TrackedCommand("c1", "t1", CommandTarget.ZONE, CommandStatus.FAILED, detail = " ", sentAt = t0, updatedAt = t0).reason)
    }

    @Test
    fun lightCommandsAreDoneWhenSentAndFailOnBridgeErrors() {
        val tr = CommandTracker()
        tr.accepted("l1", "g1", CommandTarget.LIGHT_GROUP, t0)
        val s = tr.onUpdate(CommandUpdate("l1", "sent", ""), t0.plusSeconds(1))!!
        assertEquals("Verzonden", s.label)
        assertEquals(CommandLabel.SENT, s.labelCode)
        tr.accepted("l2", "g1", CommandTarget.LIGHT_GROUP, t0)
        assertEquals(CommandStatus.FAILED, tr.onUpdate(CommandUpdate("l2", "sent", "Hue Bridge meldt: lamp onbereikbaar"), t0)!!.status)
    }

    @Test
    fun matchesRequestedState() {
        assertTrue(CommandTracker.matches(ZoneCommand(mode = "heat", targetC = 21.0), zone(21.0, "heat")))
        assertFalse(CommandTracker.matches(ZoneCommand(mode = "cool"), zone(21.0, "heat")))
        assertTrue(CommandTracker.matches(ZoneCommand(mode = "off", targetC = 30.0), zone(21.0, "off")))
        assertFalse(CommandTracker.matches(null, zone()))
    }

    @OptIn(ExperimentalCoroutinesApi::class)
    @Test
    fun debounceSendsOneFinalValue() = runTest {
        val sent = mutableListOf<Pair<String, Double>>()
        val d = Debouncer<String, Double>(this, 800) { k, v -> sent += k to v }
        d.submit("z1", 21.5)
        advanceTimeBy(300)
        d.submit("z1", 22.0)
        advanceTimeBy(300)
        d.submit("z1", 22.5)
        d.submit("z2", 19.0)
        assertTrue(d.isWaiting("z1"))
        advanceTimeBy(799)
        assertTrue(sent.isEmpty())
        advanceTimeBy(2)
        runCurrent()
        assertEquals(listOf("z1" to 22.5, "z2" to 19.0), sent)
        assertFalse(d.isWaiting("z1"))

        d.submit("z1", 23.0)
        d.cancel("z1")
        advanceTimeBy(1000)
        runCurrent()
        assertEquals(2, sent.size)
    }
}
