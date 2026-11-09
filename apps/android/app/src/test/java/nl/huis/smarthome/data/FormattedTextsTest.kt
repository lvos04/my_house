package nl.huis.smarthome.data

import nl.huis.smarthome.core.DataAge
import nl.huis.smarthome.core.Freshness
import nl.huis.smarthome.core.RelativeTime
import nl.huis.smarthome.core.StatusItem
import nl.huis.smarthome.core.WeekSchedule
import nl.huis.smarthome.ui.SystemLineUi
import org.junit.Assert.assertEquals
import org.junit.Assume.assumeTrue
import org.junit.Before
import org.junit.Test
import java.time.Instant
import java.time.LocalDate
import java.time.LocalTime
import java.time.ZoneId

/**
 * :core reports times, ages and schedule days as numbers; the app words them from strings.xml
 * (verbeterlijst, kleine punten "Toegankelijkheid Android"). These tests read strings.xml itself,
 * so they check the Dutch the user really sees.
 */
class FormattedTextsTest {
    private lateinit var t: TextSource

    @Before
    fun readStringsXml() {
        // Android unit tests run in the module directory; elsewhere there is nothing to read.
        assumeTrue(StringsXmlTexts.FILE.exists())
        t = StringsXmlTexts()
    }

    private fun relative(time: RelativeTime) = CoreTexts.relative(t, time)

    @Test
    fun relativeTimesInDutch() {
        assertEquals("onbekend", relative(RelativeTime.Unknown))
        assertEquals("zojuist", relative(RelativeTime.JustNow))
        assertEquals("1 min geleden", relative(RelativeTime.MinutesAgo(1)))
        assertEquals("3 min geleden", relative(RelativeTime.MinutesAgo(3)))
        assertEquals("1 uur geleden", relative(RelativeTime.HoursAgo(1)))
        assertEquals("2 uur geleden", relative(RelativeTime.HoursAgo(2)))
        assertEquals("1 dag geleden", relative(RelativeTime.DaysAgo(1)))
        assertEquals("9 dagen geleden", relative(RelativeTime.DaysAgo(9)))
        assertEquals("vandaag 08:05", relative(RelativeTime.Today(LocalTime.of(8, 5))))
        assertEquals("gisteren 14:05", relative(RelativeTime.Yesterday(LocalTime.of(14, 5))))
        assertEquals("3 nov 09:12", relative(RelativeTime.OnDate(LocalDate.of(2026, 11, 3), LocalTime.of(9, 12))))
        assertEquals("31 mrt 23:59", relative(RelativeTime.OnDate(LocalDate.of(2026, 3, 31), LocalTime.of(23, 59))))

        // From a server time, as the screens do.
        val now = Instant.parse("2026-11-08T13:40:00Z")
        val ams = ZoneId.of("Europe/Amsterdam")
        assertEquals("3 min geleden", relative(RelativeTime.of("2026-11-08T13:37:00Z", now, ams)))
        assertEquals("gisteren 14:05", relative(RelativeTime.of("2026-11-07T13:05:00Z", now, ams)))
    }

    @Test
    fun widgetAgeInDutch() {
        val now = Instant.parse("2026-11-08T13:40:00Z")
        val ams = ZoneId.of("Europe/Amsterdam")
        assertEquals("Bijgewerkt 3 min geleden", CoreTexts.age(t, Freshness.age("2026-11-08T13:37:00Z", now, ams)))
        assertEquals("Bijgewerkt gisteren 14:05", CoreTexts.age(t, Freshness.age("2026-11-07T13:05:00Z", now, ams)))
        assertEquals("Nog niet bijgewerkt", CoreTexts.age(t, Freshness.age(null, now, ams)))
        assertEquals("Nog niet bijgewerkt", CoreTexts.age(t, DataAge.Never))
    }

    @Test
    fun scheduleDaysInDutch() {
        fun days(vararg d: Int, spoken: Boolean = false) = CoreTexts.days(t, WeekSchedule.dayParts(d.toList()), spoken)
        assertEquals("ma–vr", days(0, 1, 2, 3, 4))
        assertEquals("maandag t/m vrijdag", days(0, 1, 2, 3, 4, spoken = true))
        assertEquals("za en zo", days(6, 5))
        assertEquals("zaterdag en zondag", days(5, 6, spoken = true))
        assertEquals("ma, wo en vr", days(0, 2, 4))
        assertEquals("ma–wo en za", days(0, 1, 2, 5))
        assertEquals("ma, di, do en vr", days(0, 1, 3, 4))
        assertEquals("elke dag", days(0, 1, 2, 3, 4, 5, 6))
        assertEquals("elke dag", days(0, 1, 2, 3, 4, 5, 6, spoken = true))
        assertEquals("do", days(3))
        assertEquals("donderdag", days(3, spoken = true))
        assertEquals("", days())
    }

    @Test
    fun systemLineInDutch() {
        val worker = StatusItem("worker", "Achtergronddienst", "ok", "Laatste hartslag 12 s geleden", ageS = 12.0)
        val backup = StatusItem("backup", "Back-up", "ok", "Laatste back-up 5 uur geleden", ageH = 5.2)
        val safe = StatusItem("safe_mode", "Veilige modus", "warn", "Geen apparaatcommando's")
        fun line(vararg items: StatusItem) = SystemLineUi.text(t, SystemLineUi.of(items.toList()))

        assertEquals("Laatste back-up 5 uur geleden · Achtergronddienst actief", line(worker, backup))
        assertEquals("Laatste back-up 5 uur geleden · Achtergronddienst actief · Veilige modus aan", line(worker, backup, safe))
        assertEquals("Laatste back-up 25 min geleden · Achtergronddienst actief", line(worker, backup.copy(ageH = 0.42)))
        assertEquals(
            "Nog geen back-up · Achtergronddienst reageert niet",
            line(StatusItem("worker", state = "error"), StatusItem("backup", state = "warn")),
        )
        assertEquals(
            "Laatste back-up 2 dagen geleden · Achtergronddienst gepauzeerd",
            line(StatusItem("worker", state = "warn"), StatusItem("maintenance", state = "warn"), backup.copy(state = "warn", ageH = 50.0)),
        )
        // Only what the server reports: an older server without the backup line.
        assertEquals("Achtergronddienst actief", line(worker))
    }
}
