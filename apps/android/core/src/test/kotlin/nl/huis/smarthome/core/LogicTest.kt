package nl.huis.smarthome.core

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import java.time.Duration
import java.time.Instant
import java.time.ZoneId
import kotlin.random.Random

class LogicTest {
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

    @Test
    fun notificationsWaitForPermissionAndDisappearWhenResolved() {
        val open = listOf(Alert("a", "warn", "Sensor verouderd"), Alert("b", "error", "Gree offline"), Alert("c", "info", "Back-up"))
        // No permission: nothing posted, nothing marked as notified, both kept pending.
        val noPerm = AlertNotifications.plan(open, emptySet(), canPost = false)
        assertTrue(noPerm.post.isEmpty())
        assertEquals(setOf("a", "b"), noPerm.pending)
        assertTrue(noPerm.notified.isEmpty())
        // Permission granted later: the same alerts are shown then.
        val later = AlertNotifications.plan(open, noPerm.notified, canPost = true)
        assertEquals(listOf("a", "b"), later.post.map { it.id })
        assertEquals(setOf("a", "b"), later.notified)
        // "a" resolved, "b" acknowledged: both notifications are cancelled.
        val after = AlertNotifications.plan(listOf(Alert("b", "error", "Gree offline", acknowledged = true)), later.notified, canPost = true)
        assertEquals(setOf("a", "b"), after.cancel)
        assertTrue(after.post.isEmpty())
        assertTrue(after.notified.isEmpty())
    }

    @Test
    fun refreshesTokenOnlyInsideTheWindow() {
        val now = Instant.parse("2026-11-08T12:00:00Z")
        assertFalse(TokenLifetime.shouldRefresh(null, now))
        assertFalse(TokenLifetime.shouldRefresh(now.plus(Duration.ofDays(31)), now))
        assertTrue(TokenLifetime.shouldRefresh(now.plus(Duration.ofDays(29)), now))
        assertTrue(TokenLifetime.shouldRefresh(now.plus(Duration.ofDays(30)), now))
        assertFalse(TokenLifetime.shouldRefresh(now.minusSeconds(1), now)) // already expired: re-pair
        // A failed attempt is not repeated on every request.
        assertFalse(TokenLifetime.shouldRefresh(now.plus(Duration.ofDays(5)), now, lastAttempt = now.minus(Duration.ofHours(1))))
        assertTrue(TokenLifetime.shouldRefresh(now.plus(Duration.ofDays(5)), now, lastAttempt = now.minus(Duration.ofHours(7))))
    }

    @Test
    fun controlRightsFollowUserAndPhoneRole() {
        assertTrue(TokenLifetime.canControl("admin", "control"))
        assertTrue(TokenLifetime.canControl("admin", "admin"))
        assertTrue(TokenLifetime.canControl("admin", null)) // paired before phone roles
        assertFalse(TokenLifetime.canControl("admin", "viewer"))
        assertFalse(TokenLifetime.canControl("viewer", "control"))
    }

    @Test
    fun widgetDataGetsStaleAfterHalfAnHour() {
        val now = Instant.parse("2026-11-08T13:40:00Z")
        val ams = ZoneId.of("Europe/Amsterdam")
        assertFalse(Freshness.isStale("2026-11-08T13:20:00Z", now))
        assertTrue(Freshness.isStale("2026-11-08T13:09:00Z", now))
        assertTrue(Freshness.isStale(null, now))
        assertTrue(Freshness.isStale("gisteren", now))
        assertEquals("Bijgewerkt 3 min geleden", Freshness.ageLabel("2026-11-08T13:37:00Z", now, ams))
        assertEquals("Bijgewerkt gisteren 14:05", Freshness.ageLabel("2026-11-07T13:05:00Z", now, ams))
        assertEquals("Nog niet bijgewerkt", Freshness.ageLabel(null, now, ams))
    }
}
