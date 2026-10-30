package com.georgewaller.audiobrain

import com.georgewaller.audiobrain.data.MixList
import com.georgewaller.audiobrain.design.AgeTag
import com.georgewaller.audiobrain.design.AgeTone
import java.time.Instant
import java.text.NumberFormat
import java.util.Locale

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

/** The tag on a list's row in its section: "Now", "Lately", the era's years ("2018–24"), "Retired"... None under
 * Yours, whose titles already say what they are. */
fun ListAge.tag(list: MixList): AgeTag? = when (this) {
    ListAge.Yours -> null
    ListAge.Now -> AgeTag("Now", AgeTone.Now)
    ListAge.Lately -> AgeTag("Lately", AgeTone.Lately)
    ListAge.Era -> AgeTag(eraSpan(list.context?.eraStart, list.context?.eraEnd) ?: "Era", AgeTone.Era)
    ListAge.Kept -> AgeTag("Retired", AgeTone.Retired)
    ListAge.New -> AgeTag("New", AgeTone.New)
    ListAge.Habit -> AgeTag("Habit", AgeTone.Habit)
    ListAge.Year -> null
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

/** The tag on a Home lead, by the kind the server gives it (recommend.actions.quick_actions); none for the rest. */
fun leadTag(kind: String): AgeTag? = when (kind) {
    "now" -> AgeTag("Now", AgeTone.Now)
    "interest" -> AgeTag("Interest", AgeTone.New)
    "explore" -> AgeTag("Explore", AgeTone.New)
    "faded" -> AgeTag("Faded", AgeTone.Lately)
    "fresh" -> AgeTag("New", AgeTone.New)
    "era" -> AgeTag("Then", AgeTone.Era)
    else -> null
}

private fun count(n: Int) = NumberFormat.getIntegerInstance(Locale.UK).format(n)

private fun parseInstant(text: String): Instant? = runCatching { java.time.OffsetDateTime.parse(text).toInstant() }.getOrNull()
