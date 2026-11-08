package nl.huis.smarthome.core

import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.time.Instant
import java.time.ZoneId
import kotlin.random.Random

class LogicTest {
    @Test
    fun prefersLanThenRemote() = runTest {
        val probed = mutableListOf<RouteKind>()
        val r = RouteSelector.select("http://lan", "https://remote") { _, k -> probed += k; k == RouteKind.REMOTE }
        assertEquals(Route(RouteKind.REMOTE, "https://remote"), r)
        assertEquals(listOf(RouteKind.LAN, RouteKind.REMOTE), probed)

        val lan = RouteSelector.select("http://lan", "https://remote") { _, _ -> true }
        assertEquals(RouteKind.LAN, lan?.kind)

        assertNull(RouteSelector.select("http://lan", null) { _, _ -> false })
    }

    @Test
    fun formatsDutchNumbers() {
        assertEquals("20,6°", NlFormat.temperature(20.6))
        assertEquals("21,0°", NlFormat.temperature(21.0))
        assertEquals("1.240 W", NlFormat.watts(1240.0))
        assertEquals("12,5 kW", NlFormat.watts(12_500.0))
        assertEquals("8,4 kWh", NlFormat.kwh(8.4))
        assertEquals("1.234 kWh", NlFormat.kwh(1234.4))
        assertEquals("€ 2,18", NlFormat.euro(2.18))
        assertEquals("€ 1.234,50", NlFormat.euro(1234.5))
        assertEquals("–", NlFormat.temperature(null))
        assertEquals("gemeten", NlFormat.quality(Quality.MEASURED))
        assertEquals("afgeleid", NlFormat.quality(Quality.DERIVED))
    }

    @Test
    fun formatsRelativeTimes() {
        val now = Instant.parse("2026-11-08T13:40:00Z")
        val ams = ZoneId.of("Europe/Amsterdam")
        assertEquals("zojuist", NlFormat.relative("2026-11-08T13:39:40Z", now, ams))
        assertEquals("3 min geleden", NlFormat.relative("2026-11-08T13:37:00Z", now, ams))
        assertEquals("2 uur geleden", NlFormat.relative("2026-11-08T11:30:00Z", now, ams))
        assertEquals("gisteren 14:05", NlFormat.relative("2026-11-07T13:05:00Z", now, ams))
        assertEquals("onbekend", NlFormat.relative(null, now, ams))
        assertEquals("14:40", NlFormat.clock("2026-11-08T13:40:00Z", ams))
    }

    @Test
    fun stepsSetpointWithinCapabilities() {
        val caps = ZoneCapabilities(minC = 16.0, maxC = 30.0, stepC = 0.5)
        assertEquals(21.5, Setpoint.step(21.0, 0.5, caps), 0.0001)
        assertEquals(30.0, Setpoint.step(30.0, 0.5, caps), 0.0001)
        assertEquals(16.0, Setpoint.step(16.2, -0.5, caps), 0.0001)
        assertEquals(21.0, Setpoint.step(20.8, 0.0, caps), 0.0001)
        assertEquals(23.5, Setpoint.step(null, 0.5, caps), 0.0001)
    }

    @Test
    fun pendingCommandClearsOnlyOnMatchingReadback() {
        val p = PendingCommands()
        val t0 = Instant.parse("2026-11-08T13:40:00Z")
        p.sent("z1", 21.5, null, t0)
        assertTrue(p.isPending("z1", t0.plusSeconds(1)))
        assertFalse(p.onZoneUpdate(Zone(id = "z1", name = "W", targetC = 21.0)))
        assertTrue(p.isPending("z1", t0.plusSeconds(2)))
        assertTrue(p.onZoneUpdate(Zone(id = "z1", name = "W", targetC = 21.5)))
        assertFalse(p.isPending("z1", t0.plusSeconds(3)))
    }

    @Test
    fun pendingCommandTimesOut() {
        val p = PendingCommands()
        val t0 = Instant.parse("2026-11-08T13:40:00Z")
        p.sent("z1", null, "heat", t0)
        assertFalse(p.isPending("z1", t0.plusSeconds(31)))
    }

    @Test
    fun backoffGrowsAndCaps() {
        val b = Backoff(initialMs = 1000, maxMs = 60_000, random = Random(1))
        val delays = List(10) { b.nextDelayMs() }
        assertTrue(delays.first() in 800..1200)
        assertTrue(delays.last() in 48_000..60_000)
        b.reset()
        assertTrue(b.nextDelayMs() in 800..1200)
    }

    @Test
    fun selectsAlertsToNotify() {
        val alerts = listOf(
            Alert("a", "warn", "Sensor verouderd"),
            Alert("b", "info", "Back-up geslaagd"),
            Alert("c", "error", "Bridge offline", acknowledged = true),
            Alert("d", "error", "Gree offline"),
        )
        assertEquals(listOf("d"), AlertFilter.toNotify(alerts, setOf("a")).map { it.id })
    }
}
