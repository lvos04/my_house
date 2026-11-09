package nl.huis.smarthome.core

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import java.time.DayOfWeek
import java.time.LocalDateTime
import java.util.Locale

// ---------------------------------------------------------------------------------------------
// Week schedule of a zone (verbeterlijst 12): GET /zones/{id}/schedule, shown read-only in the
// app (the editor is in the web app). Days are 0 = Monday … 6 = Sunday, as on the server.
// ---------------------------------------------------------------------------------------------

/** One schedule block; without [mode] it keeps the mode the unit runs in (never starts it). */
@Serializable
data class ScheduleBlock(
    val days: List<Int> = emptyList(),
    /** "HH:MM" in the home's time zone. */
    val start: String,
    @SerialName("target_c") val targetC: Double,
    val mode: String? = null,
    val label: String = "",
)

/** `GET /zones/{id}/schedule`: the zone's own blocks (also when it follows a profile). */
@Serializable
data class ZoneSchedule(
    val blocks: List<ScheduleBlock> = emptyList(),
    @SerialName("profile_id") val profileId: String? = null,
    /** Name of the comfort profile the zone follows, null for an own schedule. */
    val profile: String? = null,
)

/** A block as it applies to one day. */
data class DayEntry(val start: String, val targetC: Double, val mode: String?, val label: String)

/** Days with exactly the same blocks, shown together ("ma–vr"). */
data class DayGroup(val days: List<Int>, val entries: List<DayEntry>)

/** The block in force at a moment: [day] is the day whose block it is (maybe yesterday's). */
data class ActiveBlock(val day: Int, val entry: DayEntry)

/**
 * The days of a [DayGroup], for the app to word from strings.xml: "elke dag", "ma–vr",
 * "za en zo", "ma, wo en vr" (and the full names for TalkBack).
 */
sealed interface DayParts {
    /** No valid days. */
    data object None : DayParts

    /** All seven days ("elke dag"). */
    data object EveryDay : DayParts

    /** In week order; the app joins them as "ma, wo en vr". */
    data class Listed(val parts: List<DayPart>) : DayParts
}

sealed interface DayPart {
    /** One day ("wo", spoken "woensdag"). */
    data class Single(val day: DayOfWeek) : DayPart

    /** Three or more days in a row ("ma–vr", spoken "maandag t/m vrijdag"). */
    data class Range(val first: DayOfWeek, val last: DayOfWeek) : DayPart
}

object WeekSchedule {
    private val TIME = Regex("^([01]\\d|2[0-3]):[0-5]\\d$")

    /** The blocks that start on [day], in time order (invalid times and days are left out). */
    fun day(blocks: List<ScheduleBlock>, day: Int): List<DayEntry> = blocks
        .filter { day in it.days && TIME.matches(it.start) }
        .map { DayEntry(it.start, it.targetC, it.mode, it.label) }
        .sortedBy { it.start }

    /**
     * Monday to Sunday, with days that have the same blocks merged into one group. Groups are in
     * the order of their first day; a day without blocks of its own is a group with no entries.
     */
    fun groups(blocks: List<ScheduleBlock>): List<DayGroup> {
        val perDay = (0..6).map { day(blocks, it) }
        val out = mutableListOf<DayGroup>()
        for (d in 0..6) {
            if (out.any { d in it.days }) continue
            val same = (d..6).filter { perDay[it] == perDay[d] }
            out += DayGroup(same, perDay[d])
        }
        return out
    }

    /**
     * The days 0 = Monday … 6 = Sunday as parts: a run of three or more days in a row becomes a
     * range ("ma–vr"), shorter runs stay single days ("za en zo"); all seven is "elke dag".
     */
    fun dayParts(days: List<Int>): DayParts {
        val sorted = days.filter { it in 0..6 }.distinct().sorted()
        if (sorted.isEmpty()) return DayParts.None
        if (sorted.size == 7) return DayParts.EveryDay
        val runs = mutableListOf<List<Int>>()
        for (d in sorted) {
            val last = runs.lastOrNull()
            if (last != null && last.last() == d - 1) runs[runs.size - 1] = last + d else runs += listOf(d)
        }
        val parts = runs.flatMap { run ->
            if (run.size >= 3) {
                listOf(DayPart.Range(dayOfWeek(run.first()), dayOfWeek(run.last())))
            } else {
                run.map { DayPart.Single(dayOfWeek(it)) }
            }
        }
        return DayParts.Listed(parts)
    }

    /** The server's day number (0 = Monday … 6 = Sunday) as a [DayOfWeek]. */
    fun dayOfWeek(day: Int): DayOfWeek = DayOfWeek.of(day + 1)

    /**
     * The block in force at [now] (local time of the home): the last block that started today at
     * or before now, else the last block of an earlier day (up to a week back). Null without blocks.
     */
    fun active(blocks: List<ScheduleBlock>, now: LocalDateTime): ActiveBlock? {
        val today = now.dayOfWeek.value - 1
        val time = String.format(Locale.ROOT, "%02d:%02d", now.hour, now.minute)
        day(blocks, today).lastOrNull { it.start <= time }?.let { return ActiveBlock(today, it) }
        for (back in 1..7) {
            val d = ((today - back) % 7 + 7) % 7
            day(blocks, d).lastOrNull()?.let { return ActiveBlock(d, it) }
        }
        return null
    }
}
