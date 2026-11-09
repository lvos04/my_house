package nl.huis.smarthome.core

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import java.time.Duration
import java.time.Instant
import java.time.LocalDate
import java.time.LocalTime
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
    fun relativeTimesAreNumbersForTheAppToWord() {
        val now = Instant.parse("2026-11-08T13:40:00Z")
        val ams = ZoneId.of("Europe/Amsterdam")
        assertEquals(RelativeTime.JustNow, RelativeTime.of("2026-11-08T13:39:40Z", now, ams))
        assertEquals(RelativeTime.JustNow, RelativeTime.of("2026-11-08T13:41:00Z", now, ams)) // phone clock behind
        assertEquals(RelativeTime.MinutesAgo(3), RelativeTime.of("2026-11-08T13:37:00Z", now, ams))
        assertEquals(RelativeTime.MinutesAgo(1), RelativeTime.of("2026-11-08T13:39:10Z", now, ams))
        assertEquals(RelativeTime.HoursAgo(2), RelativeTime.of("2026-11-08T11:30:00Z", now, ams))
        assertEquals(RelativeTime.HoursAgo(5), RelativeTime.of("2026-11-08T07:41:00Z", now, ams))
        // 6 hours or more: the clock time in the home's zone (UTC+1 in November).
        assertEquals(RelativeTime.Today(LocalTime.of(8, 5)), RelativeTime.of("2026-11-08T07:05:30Z", now, ams))
        assertEquals(RelativeTime.Yesterday(LocalTime.of(14, 5)), RelativeTime.of("2026-11-07T13:05:00Z", now, ams))
        assertEquals(
            RelativeTime.OnDate(LocalDate.of(2026, 11, 3), LocalTime.of(9, 12)),
            RelativeTime.of("2026-11-03T08:12:00Z", now, ams),
        )
        // Today and yesterday follow the home's calendar day, not UTC.
        val morning = Instant.parse("2026-11-09T07:00:00Z") // 08:00 on 9 November in Amsterdam
        assertEquals(RelativeTime.Today(LocalTime.of(0, 30)), RelativeTime.of("2026-11-08T23:30:00Z", morning, ams))
        assertEquals(RelativeTime.Yesterday(LocalTime.of(23, 30)), RelativeTime.of("2026-11-08T22:30:00Z", morning, ams))
        assertEquals(RelativeTime.Unknown, RelativeTime.of(null, now, ams))
        assertEquals(RelativeTime.Unknown, RelativeTime.of("gisteren", now, ams))
        assertEquals("14:40", NlFormat.clock("2026-11-08T13:40:00Z", ams))
        assertEquals("09:05", NlFormat.hhmm(LocalTime.of(9, 5)))
    }

    @Test
    fun agesAreRoundedLikeTheWebApp() {
        assertEquals(RelativeTime.Unknown, RelativeTime.ofAge(null))
        assertEquals(RelativeTime.JustNow, RelativeTime.ofAge(Duration.ofSeconds(59)))
        assertEquals(RelativeTime.JustNow, RelativeTime.ofAge(Duration.ofSeconds(-30)))
        assertEquals(RelativeTime.MinutesAgo(1), RelativeTime.ofAge(Duration.ofSeconds(60)))
        assertEquals(RelativeTime.MinutesAgo(42), RelativeTime.ofAge(Duration.ofMinutes(42)))
        // 59.5 minutes rounds to a whole hour, never "60 min".
        assertEquals(RelativeTime.HoursAgo(1), RelativeTime.ofAge(Duration.ofSeconds(3570)))
        assertEquals(RelativeTime.HoursAgo(5), RelativeTime.ofAge(Duration.ofMinutes(5 * 60 + 12)))
        assertEquals(RelativeTime.HoursAgo(6), RelativeTime.ofAge(Duration.ofMinutes(5 * 60 + 45)))
        assertEquals(RelativeTime.HoursAgo(47), RelativeTime.ofAge(Duration.ofHours(47)))
        assertEquals(RelativeTime.DaysAgo(2), RelativeTime.ofAge(Duration.ofHours(48)))
        assertEquals(RelativeTime.DaysAgo(3), RelativeTime.ofAge(Duration.ofHours(70)))
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
        assertEquals(DataAge.Updated(RelativeTime.MinutesAgo(3)), Freshness.age("2026-11-08T13:37:00Z", now, ams))
        assertEquals(DataAge.Updated(RelativeTime.Yesterday(LocalTime.of(14, 5))), Freshness.age("2026-11-07T13:05:00Z", now, ams))
        assertEquals(DataAge.Never, Freshness.age(null, now, ams))
        assertEquals(DataAge.Never, Freshness.age("gisteren", now, ams))
    }
}
