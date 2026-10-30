package com.georgewaller.audiobrain

import com.georgewaller.audiobrain.data.MixList
import com.georgewaller.audiobrain.design.AgeTag
import java.time.Instant
import java.time.LocalDateTime
import java.text.NumberFormat
import java.util.Locale
import kotlin.math.ln

/** The Lists page's sections, in order, from what you're playing now to what's older. */
enum class ListAge(val title: String, val caption: String) {
    Yours("Yours", "Lists you asked for, and artists you're exploring"),
    Now("Playing now", "What you've played most in the last 45 days"),
    Lately("Also in rotation", "Played lately, just less"),
    Era("From earlier eras", "Big for you once, quiet lately; a few come round each week"),
    Kept("Kept as they were", "Their context has retired; kept because they're in Tidal"),
    New("New music", "Tracks you haven't played"),
    Habit("Habits", "What you replay, and what you've left behind"),
    Year("Your years", "What you played most each year"),
}

/** How many context lists count as "Playing now"; the rest played lately are "Also in rotation". */
const val PLAYING_NOW = 6

val YOURS = setOf("radio", "asked", "explore", "interest", "set")

/** Lists into the Lists page's sections, each in its order: contexts by plays lately, years newest first. */
fun byAge(lists: List<MixList>): List<Pair<ListAge, List<MixList>>> {
    val sections = lists.groupBy { list ->
        when {
            list.kind in YOURS -> ListAge.Yours
            list.context?.active == false -> ListAge.Kept
            list.kind == "mix" -> ListAge.Lately  // the most played become Now, below
            list.kind == "era" -> ListAge.Era
            list.kind in setOf("fresh", "new", "edges") -> ListAge.New
            list.kind == "year" -> ListAge.Year
            else -> ListAge.Habit
        }
    }.toMutableMap()
    val contexts = sections[ListAge.Lately].orEmpty().sortedByDescending { it.context?.recentPlays ?: 0 }
    sections[ListAge.Now] = contexts.take(PLAYING_NOW)
    sections[ListAge.Lately] = contexts.drop(PLAYING_NOW)
    sections[ListAge.Era] = sections[ListAge.Era].orEmpty().sortedByDescending { it.context?.eraEnd ?: "" }
    sections[ListAge.Year] = sections[ListAge.Year].orEmpty().sortedByDescending { it.key }
    return ListAge.entries.mapNotNull { age -> sections[age]?.takeIf { it.isNotEmpty() }?.let { age to it } }
}

/** The tag on a list's row in its section: "Now", "Lately", the era's years ("2018–24"), "Retired"... */
fun ListAge.tag(list: MixList): AgeTag = when (this) {
    ListAge.Yours -> AgeTag(list.kindLabel())
    ListAge.Now -> AgeTag("Now", current = true)
    ListAge.Lately -> AgeTag("Lately")
    ListAge.Era -> AgeTag(eraSpan(list.context?.eraStart, list.context?.eraEnd) ?: "Era")
    ListAge.Kept -> AgeTag("Retired")
    ListAge.New -> AgeTag("New")
    ListAge.Habit -> AgeTag("Habit")
    ListAge.Year -> AgeTag("Year")
}

/** The line under a list's title in its section: how much you've played it lately, or when its era was. */
fun ListAge.meta(list: MixList, now: Instant = Instant.now()): String {
    val context = list.context ?: return list.meta()
    return when (this) {
        ListAge.Now, ListAge.Lately -> "${count(context.recentPlays)} plays lately" +
            (context.lastPlayed?.let(::parseInstant)?.let { " · last ${relative(it, now)}" } ?: "")
        ListAge.Era -> "${count(context.lifetimePlays)} plays · ${count(context.recentPlays)} lately"
        ListAge.Kept -> "Kept as it last was · ${list.meta()}"
        else -> list.meta()
    }
}

/** "2018–24" from the era's dates; null without them. */
fun eraSpan(start: String?, end: String?): String? {
    val from = start?.take(4)?.toIntOrNull() ?: return null
    val to = end?.take(4)?.toIntOrNull() ?: return null
    return if (from == to) "$from" else "$from–${"%02d".format(to % 100)}"
}

/** A context list that suits the moment, and why: Home's "Fits now". */
data class Fit(val list: MixList, val lift: Float, val reason: String)

/**
 * Context lists ranked by how much more than usual you play them at this hour (and on this day), weighted by how
 * much you've played them lately, so a context you only played twice at 10:00 doesn't top a favourite. The hour is
 * smoothed with its neighbours, as a single hour's share is noisy.
 */
fun fitsNow(lists: List<MixList>, at: LocalDateTime, count: Int = 3): List<Fit> {
    val hour = at.hour
    val day = at.dayOfWeek.value - 1  // Monday first, as the server sends them
    return lists.filter { it.kind == "mix" && it.context?.active == true && it.context?.hours?.size == 24 }
        .map { list ->
            val c = list.context!!
            val hours = c.hours
            val hourLift = (hours[(hour + 23) % 24] + hours[hour] + hours[(hour + 1) % 24]) / 3f * 24f
            val dayLift = c.weekdays.getOrNull(day)?.let { it * 7f } ?: 1f
            val lift = hourLift * dayLift
            Triple(list, lift, lift * ln(1f + c.recentPlays))
        }
        .sortedByDescending { it.third }
        .take(count)
        .map { (list, lift, _) -> Fit(list, lift, reason(list, lift, at)) }
}

private fun reason(list: MixList, lift: Float, at: LocalDateTime): String {
    val c = list.context!!
    val time = "%02d:00".format(at.hour)
    val day = at.dayOfWeek.getDisplayName(java.time.format.TextStyle.FULL, Locale.UK)
    val dayLift = c.weekdays.getOrNull(at.dayOfWeek.value - 1)?.let { it * 7f } ?: 1f
    val usual = if (lift >= 1.05f) "%.1f× your usual around $time".format(Locale.UK, lift) else "As much as usual around $time"
    val onDay = if (dayLift >= 1.15f) ", more on ${day}s" else ""
    return "$usual$onDay · ${count(c.recentPlays)} plays lately"
}

private fun count(n: Int) = NumberFormat.getIntegerInstance(Locale.UK).format(n)

private fun parseInstant(text: String): Instant? = runCatching { java.time.OffsetDateTime.parse(text).toInstant() }.getOrNull()
