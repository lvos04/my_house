package nl.huis.smarthome

import nl.huis.smarthome.core.Route
import nl.huis.smarthome.core.RouteKind
import nl.huis.smarthome.data.HomeState
import nl.huis.smarthome.data.Pairing
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class HomeStateTest {
    private val pairing = Pairing("shc_x", "http://lan", null, "h1", "Thuis", "telefoon", "admin")

    @Test
    fun controlsOnlyWhenOnlineAndAuthorized() {
        assertFalse(HomeState().paired)
        val s = HomeState(pairing = pairing, route = Route(RouteKind.LAN, "http://lan"), online = true)
        assertTrue(s.paired)
        assertTrue(s.controlsEnabled)
        assertFalse(s.copy(online = false).controlsEnabled)
        assertFalse(s.copy(needsRepair = true).controlsEnabled)
    }
}
