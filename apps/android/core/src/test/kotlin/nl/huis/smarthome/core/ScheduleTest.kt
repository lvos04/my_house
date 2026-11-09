package nl.huis.smarthome.core

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test
import java.time.DayOfWeek
import java.time.DayOfWeek.FRIDAY
import java.time.DayOfWeek.MONDAY
import java.time.DayOfWeek.SATURDAY
import java.time.DayOfWeek.SUNDAY
import java.time.DayOfWeek.THURSDAY
import java.time.DayOfWeek.TUESDAY
import java.time.DayOfWeek.WEDNESDAY
import java.time.LocalDateTime

class ScheduleTest {
    private val weekdays = listOf(0, 1, 2, 3, 4)
    private val blocks = listOf(
        ScheduleBlock(weekdays, "07:00", 21.0, "heat", "Ochtend"),
        ScheduleBlock(weekdays, "22:30", 17.5, null, "Nacht"),
        ScheduleBlock(listOf(5, 6), "08:30", 20.5, "heat", ""),
        ScheduleBlock(listOf(5, 6), "23:00", 18.0, "off", ""),
    )

    @Test
    fun parsesTheServerAnswer() {
        val json = """{"blocks":[{"days":[0,1],"start":"07:00","target_c":21,"mode":null,"label":"Ochtend"}],"profile_id":"p1","profile":"Comfort"}"""
        val s = ShcJson.decodeFromString(ZoneSchedule.serializer(), json)
        assertEquals("Comfort", s.profile)
        assertEquals(ScheduleBlock(listOf(0, 1), "07:00", 21.0, null, "Ochtend"), s.blocks.single())
    }

    @Test
    fun groupsDaysWithTheSameBlocks() {
        val groups = WeekSchedule.groups(blocks)
        assertEquals(listOf(weekdays, listOf(5, 6)), groups.map { it.days })
        assertEquals(listOf("07:00", "22:30"), groups[0].entries.map { it.start })
        assertEquals(listOf("08:30", "23:00"), groups[1].entries.map { it.start })

        // A day without blocks of its own is shown as such; out-of-order input is sorted.
        val partial = WeekSchedule.groups(listOf(ScheduleBlock(listOf(2), "18:00", 20.0), ScheduleBlock(listOf(2), "06:00", 19.0)))
        assertEquals(listOf(listOf(0, 1, 3, 4, 5, 6), listOf(2)), partial.map { it.days })
        assertEquals(emptyList<DayEntry>(), partial[0].entries)
        assertEquals(listOf("06:00", "18:00"), partial[1].entries.map { it.start })

        assertEquals(listOf((0..6).toList()), WeekSchedule.groups(emptyList()).map { it.days })
    }

    @Test
    fun dayPartsAreRangesAndSingleDays() {
        fun single(d: DayOfWeek) = DayPart.Single(d)
        assertEquals(DayParts.Listed(listOf(DayPart.Range(MONDAY, FRIDAY))), WeekSchedule.dayParts(weekdays))
        assertEquals(DayParts.Listed(listOf(single(SATURDAY), single(SUNDAY))), WeekSchedule.dayParts(listOf(6, 5)))
        assertEquals(DayParts.Listed(listOf(single(MONDAY), single(WEDNESDAY), single(FRIDAY))), WeekSchedule.dayParts(listOf(0, 2, 4)))
        assertEquals(DayParts.Listed(listOf(DayPart.Range(MONDAY, WEDNESDAY), single(SATURDAY))), WeekSchedule.dayParts(listOf(0, 1, 2, 5)))
        // Two days in a row stay two single days ("ma en di"), not a range.
        assertEquals(DayParts.Listed(listOf(single(MONDAY), single(TUESDAY))), WeekSchedule.dayParts(listOf(1, 0, 1)))
        assertEquals(DayParts.Listed(listOf(single(THURSDAY))), WeekSchedule.dayParts(listOf(3)))
        assertEquals(DayParts.EveryDay, WeekSchedule.dayParts((0..6).toList()))
        assertEquals(DayParts.EveryDay, WeekSchedule.dayParts((0..6).toList() + 7))
        assertEquals(DayParts.None, WeekSchedule.dayParts(emptyList()))
        assertEquals(DayParts.None, WeekSchedule.dayParts(listOf(7, -1)))
        assertEquals(SUNDAY, WeekSchedule.dayOfWeek(6))
    }

    @Test
    fun blockInForce() {
        // Wednesday 2026-11-11.
        val wed = LocalDateTime.of(2026, 11, 11, 12, 0)
        assertEquals(ActiveBlock(2, DayEntry("07:00", 21.0, "heat", "Ochtend")), WeekSchedule.active(blocks, wed))
        // Before the first block of the day: yesterday's last block still holds.
        assertEquals("22:30", WeekSchedule.active(blocks, wed.withHour(6)).let { it!!.day to it.entry.start }.second)
        assertEquals(1, WeekSchedule.active(blocks, wed.withHour(6))!!.day)
        // Monday early: Sunday's block holds (the week wraps).
        val mon = LocalDateTime.of(2026, 11, 9, 5, 0)
        assertEquals(ActiveBlock(6, DayEntry("23:00", 18.0, "off", "")), WeekSchedule.active(blocks, mon))
        // Exactly at the start time the new block holds.
        assertEquals("22:30", WeekSchedule.active(blocks, wed.withHour(22).withMinute(30))!!.entry.start)
        assertNull(WeekSchedule.active(emptyList(), wed))
    }
}
