package nl.huis.smarthome

import nl.huis.smarthome.core.CommandStatus
import nl.huis.smarthome.core.CommandTarget
import nl.huis.smarthome.core.Route
import nl.huis.smarthome.core.RouteKind
import nl.huis.smarthome.core.TrackedCommand
import nl.huis.smarthome.core.UnpairReason
import nl.huis.smarthome.core.Zone
import nl.huis.smarthome.core.ZoneCommand
import nl.huis.smarthome.data.HomeState
import nl.huis.smarthome.data.Pairing
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import java.time.Instant

class HomeStateTest {
    private val pairing = Pairing("shc_x", "http://lan", null, "h1", "Thuis", "telefoon", "admin")

    @Test
    fun controlsOnlyWhenOnlineAndAuthorized() {
        assertFalse(HomeState().paired)
        val s = HomeState(pairing = pairing, route = Route(RouteKind.LAN, "http://lan"), online = true)
        assertTrue(s.paired)
        assertTrue(s.controlsEnabled)
        assertFalse(s.copy(online = false).controlsEnabled)
        assertFalse(s.copy(unpaired = UnpairReason.REVOKED).controlsEnabled)
        assertTrue(s.copy(unpaired = UnpairReason.EXPIRED).needsRepair)
    }

    @Test
    fun viewerPhoneIsReadOnly() {
        val viewer = HomeState(pairing = pairing.copy(clientRole = "viewer"), online = true)
        assertTrue(viewer.readOnly)
        assertFalse(viewer.controlsEnabled)
        assertFalse(viewer.isAdmin)
        // The server's answer (/auth/me, hello) wins over the stored roles.
        val told = HomeState(pairing = pairing.copy(clientRole = "control", canControl = false), online = true)
        assertTrue(told.readOnly)
        val control = HomeState(pairing = pairing.copy(clientRole = "control"), online = true)
        assertTrue(control.controlsEnabled)
        assertFalse(control.isAdmin)
        assertTrue(HomeState(pairing = pairing.copy(clientRole = "admin")).isAdmin)
    }

    @Test
    fun showsDraftThenRequestedTargetUntilConfirmed() {
        val zone = Zone(id = "z1", name = "Woonkamer", targetC = 21.0)
        val t = Instant.parse("2026-11-08T13:40:00Z")
        val cmd = TrackedCommand("c1", "z1", CommandTarget.ZONE, CommandStatus.SENT, requested = ZoneCommand(targetC = 22.0), sentAt = t, updatedAt = t)
        val s = HomeState(commands = mapOf("z1" to cmd))
        assertEquals(22.0, s.shownTarget(zone)!!, 0.0)
        assertEquals(22.5, s.copy(drafts = mapOf("z1" to 22.5)).shownTarget(zone)!!, 0.0)
        val done = s.copy(commands = mapOf("z1" to cmd.copy(status = CommandStatus.FAILED)))
        assertEquals(21.0, done.shownTarget(zone)!!, 0.0)
    }
}
