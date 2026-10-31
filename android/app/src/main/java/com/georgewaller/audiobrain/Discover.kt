package com.georgewaller.audiobrain

import androidx.compose.foundation.Canvas
import com.georgewaller.audiobrain.design.AbIcons
import com.georgewaller.audiobrain.design.AbDivider
import androidx.compose.ui.unit.sp
import androidx.compose.ui.graphics.Color
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.ColumnScope
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.border
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.runtime.Composable
import com.georgewaller.audiobrain.design.LocalRowGround
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.unit.dp
import com.georgewaller.audiobrain.data.ListItem
import com.georgewaller.audiobrain.data.QueueSummary
import com.georgewaller.audiobrain.data.MixList
import com.georgewaller.audiobrain.data.Then
import com.georgewaller.audiobrain.design.Ab
import com.georgewaller.audiobrain.design.AbButton
import com.georgewaller.audiobrain.design.AbText
import com.georgewaller.audiobrain.design.AbIcon
import com.georgewaller.audiobrain.design.TrackHue
import com.georgewaller.audiobrain.design.ListSwatch
import com.georgewaller.audiobrain.design.ButtonKind
import java.text.NumberFormat
import java.time.Year
import java.time.YearMonth
import java.time.format.DateTimeFormatter
import java.util.Locale

/** Where then-and-now graphs start: the first year with listening worth showing. */
const val FIRST_YEAR = 2016

/** One bar of a then-and-now graph: a year's plays, and whether it's the biggest year or this one. */
data class YearBar(val year: Int, val plays: Int, val peak: Boolean, val now: Boolean)

/** A bar per year from [from] to [to] (this year), from plays per year keyed by year ("2018"). */
fun yearBars(years: Map<String, Int>, to: Int = Year.now().value, from: Int = FIRST_YEAR): List<YearBar> {
    val plays = (from..to).associateWith { years[it.toString()] ?: 0 }
    val peak = plays.maxByOrNull { it.value }?.takeIf { it.value > 0 }?.key
    return plays.map { (year, n) -> YearBar(year, n, year == peak, year == to) }
}

/** Where a faded favourite's peak and its last play fall between [FIRST_YEAR] and now, each 0–1; null without dates. */
fun thenPositions(then: Then, now: YearMonth = YearMonth.now()): Pair<Float, Float>? {
    val start = YearMonth.of(FIRST_YEAR, 1)
    val span = (now.year - start.year) * 12 + now.monthValue - start.monthValue
    fun at(month: String) = runCatching { YearMonth.parse(month) }.getOrNull()?.let { ((it.year - start.year) * 12 + it.monthValue - start.monthValue).toFloat() / span }
    val peak = at(then.peakAt) ?: return null
    val last = at(then.lastAt) ?: return null
    return peak.coerceIn(0f, 1f) to last.coerceIn(0f, 1f)
}

/** "2016-03" -> "Mar 2016". */
fun monthLabel(month: String): String = runCatching { YearMonth.parse(month).format(MONTH) }.getOrDefault(month)

/** A release's "sounds like" tracks, from its reason ("“Signs” sounds like A – B and C – D"); null when it hasn't one. */
fun soundsLike(reason: String): String? = reason.substringAfter(" sounds like ", "").trim().ifBlank { null }

fun count(n: Int): String = NumberFormat.getIntegerInstance(Locale.UK).format(n)

private val MONTH = DateTimeFormatter.ofPattern("MMM yyyy", Locale.UK)

/**
 * A Discover section: a kicker in its tag's colour (what kind and how many), its name, why things are in it, and its
 * rows together in one card with the ways to all of it at the foot, so where one section ends is plain as you scroll.
 */
@Composable
fun DiscoverSection(kicker: String, tone: Color, title: String, caption: String, links: List<Pair<String, () -> Unit>>, content: @Composable ColumnScope.() -> Unit) {
    Column(Modifier.fillMaxWidth().padding(top = Ab.space.xxl)) {
        Column(Modifier.padding(bottom = Ab.space.s)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Box(Modifier.size(8.dp).clip(CircleShape).background(tone))
                Spacer(Modifier.width(Ab.space.s))
                AbText(kicker.uppercase(), style = Ab.type.overline, color = tone, maxLines = 1)
            }
            AbText(title, Modifier.padding(top = Ab.space.xs), style = Ab.type.display.copy(fontSize = 28.sp, lineHeight = 30.sp), maxLines = 2)
            AbText(caption, Modifier.padding(top = Ab.space.xxs), style = Ab.type.caption, color = Ab.colors.textMuted, maxLines = 2)
        }
        Column(Modifier.fillMaxWidth().clip(Ab.shapes.large).background(Ab.colors.surface).border(1.dp, Ab.colors.line, Ab.shapes.large)) {
            CompositionLocalProvider(LocalRowGround provides Ab.colors.surface) { content() }
            links.forEachIndexed { i, (label, onOpen) ->
                if (i > 0) AbDivider()
                Row(
                    Modifier.fillMaxWidth().clickable(onClick = onOpen).heightIn(min = Ab.space.touch).padding(horizontal = Ab.space.m),
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    AbText(label, Modifier.weight(1f), style = Ab.type.label, maxLines = 1)
                    AbText("→", style = Ab.type.label, color = Ab.colors.textMuted)
                }
            }
        }
    }
}

/** A row inside a [DiscoverSection]'s card: inset from its edge, with a hairline under it. */
@Composable
fun CardRow(content: @Composable () -> Unit) {
    Box(Modifier.fillMaxWidth().padding(horizontal = Ab.space.m)) { content() }
    AbDivider()
}

/** What a run of releases shows: those still to decide, up to [shown], and a line for each decided on this visit. */
sealed interface Slot {
    val item: ListItem
    /** [fresh]: it came into view because one above it was decided. */
    data class Open(override val item: ListItem, val fresh: Boolean) : Slot
    data class Decided(override val item: ListItem, val state: String) : Slot
}

/**
 * Decide a release and the next one moves up into view: the first [shown] undecided of [items], in their order, with
 * the ones decided on this visit ([here]) left in place as lines you can undo. Ones decided before are gone.
 */
fun refill(items: List<ListItem>, decided: Map<Long, String>, here: Set<Long>, shown: Int): List<Slot> {
    fun stateOf(item: ListItem) = decided[item.releaseId] ?: item.state ?: "new"
    // the order as the visit began, so a row that moves up into view can be told apart
    val before = items.filter { stateOf(it) == "new" || it.releaseId in here }
    var open = 0
    return before.mapIndexedNotNull { i, item ->
        val state = stateOf(item)
        when {
            state != "new" -> Slot.Decided(item, state)
            open < shown -> { open++; Slot.Open(item, fresh = i >= shown) }
            else -> null
        }
    }
}

/** Which of the queue's reasons each listed release is there for; a folded one shares its row's. */
fun releaseSources(lists: List<MixList>): Map<Long, String> =
    lists.filter { it.key.startsWith("releases") }.flatMap { l -> l.items.flatMap { r -> (listOf(r) + r.others).mapNotNull { o -> o.releaseId?.let { it to r.source } } } }.toMap()

/**
 * The queue's counts as the server gave them, adjusted for what's been decided (or undone) here since: [seen] is the
 * app's decisions as they stood then, [now] as they stand, [sources] why each release is in the queue.
 */
fun queueNow(summary: QueueSummary, seen: Map<Long, String>, now: Map<Long, String>, sources: Map<Long, String>): QueueSummary {
    fun decided(states: Map<Long, String>, id: Long) = (states[id] ?: "new") != "new"
    val waiting = summary.waiting.toMutableMap()
    var change = 0
    (seen.keys + now.keys).forEach { id ->
        val step = when {
            decided(now, id) && !decided(seen, id) -> 1
            !decided(now, id) && decided(seen, id) -> -1
            else -> return@forEach
        }
        change += step
        sources[id]?.let { s -> waiting[s] = ((waiting[s] ?: 0) - step).coerceAtLeast(0) }
    }
    return summary.copy(waiting = waiting, decided = summary.decided + change, decidedThisWeek = summary.decidedThisWeek + change)
}

/** A release decided on this visit: what was decided and its title, with Undo, in one line. */
@Composable
fun DecidedLine(item: ListItem, state: String, onUndo: () -> Unit) {
    val (label, color) = when (state) {
        "saved" -> "Saved" to Ab.colors.keep
        "heard" -> "Heard" to Ab.colors.textMuted
        else -> "Skipped" to Ab.colors.textMuted
    }
    Row(Modifier.fillMaxWidth().heightIn(min = Ab.space.touch), verticalAlignment = Alignment.CenterVertically) {
        AbText(label, style = Ab.type.label, color = color)
        Spacer(Modifier.width(Ab.space.s))
        AbText(item.title, Modifier.weight(1f), style = Ab.type.caption, color = Ab.colors.textMuted, maxLines = 1)
        AbText("Undo", Modifier.clickable(onClick = onUndo).heightIn(min = 44.dp).padding(start = Ab.space.m, top = Ab.space.m), style = Ab.type.label)
    }
}

/**
 * A release in the queue: its colours (what its tracks sound like), which play it in Tidal, what it is, the tracks of yours it sounds like (in
 * full), and Save and Skip under that, so the title has the width. An artist's other waiting releases are folded under
 * it, each decidable too, with a way to skip them all.
 */
@Composable
fun ReleaseCard(item: ListItem, decided: Map<Long, String>, onDecide: (ListItem, String) -> Unit, onOpen: (ListItem) -> Unit, expanded: Boolean, why: Boolean = true, fresh: Boolean = false, onExpand: () -> Unit) {
    Column(Modifier.fillMaxWidth().padding(vertical = Ab.space.m), verticalArrangement = Arrangement.spacedBy(Ab.space.xxs)) {
        Row(verticalAlignment = Alignment.Top) {
            ReleaseCover(item) { onOpen(item) }
            Spacer(Modifier.width(Ab.space.m))
            Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(Ab.space.xxs)) {
                Column(Modifier.clickable { onOpen(item) }, verticalArrangement = Arrangement.spacedBy(Ab.space.xxs)) {
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        AbText(item.title, Modifier.weight(1f, fill = false), style = Ab.type.heading, maxLines = 2)
                        if (fresh) AbText("just in", Modifier.padding(start = Ab.space.s), style = Ab.type.number, color = Ab.colors.ageNew)
                    }
                    AbText(item.artist, style = Ab.type.body, color = Ab.colors.textMuted, maxLines = 1)
                    AbText(listOf(item.detail, item.why.takeIf { why }.orEmpty()).filter { it.isNotBlank() }.joinToString(" · "), style = Ab.type.number, color = Ab.colors.textMuted, maxLines = 1)
                    soundsLike(item.reason)?.let { AbText("Sounds like $it", style = Ab.type.caption, color = Ab.colors.text, maxLines = 2) }
                }
                Row(Modifier.padding(top = Ab.space.xs), verticalAlignment = Alignment.CenterVertically) {
                    Decision(item, decided[item.releaseId], onDecide)
                    Spacer(Modifier.weight(1f))
                    if (item.othersCount > 0) AbText(
                        if (expanded) "Fold" else "+${item.othersCount} more",
                        Modifier.clickable(onClick = onExpand).heightIn(min = 44.dp).padding(start = Ab.space.s, top = Ab.space.m),
                        style = Ab.type.label, color = Ab.colors.textMuted,
                    )
                }
            }
        }
        if (item.othersCount > 0 && expanded) {
            Column(Modifier.padding(start = 64.dp)) {
                item.others.forEach { other ->
                    AbDivider()
                    Row(Modifier.fillMaxWidth().heightIn(min = 52.dp), verticalAlignment = Alignment.CenterVertically) {
                        Column(Modifier.weight(1f).clickable { onOpen(other) }) {
                            AbText(other.title, style = Ab.type.label, maxLines = 1)
                            AbText(other.detail, style = Ab.type.number, color = Ab.colors.textMuted, maxLines = 1)
                        }
                        Decision(other, decided[other.releaseId], onDecide)
                    }
                }
                val unseen = item.othersCount - item.others.size
                if (unseen > 0) AbText("…and $unseen more", style = Ab.type.caption, color = Ab.colors.textMuted)
                val open = item.others.filter { (decided[it.releaseId] ?: it.state ?: "new") == "new" }
                if (open.size > 1) AbButton(
                    if (open.size == item.othersCount) "Skip the other ${open.size}" else "Skip these ${open.size}",
                    { open.forEach { onDecide(it, "dismissed") } }, Modifier.padding(top = Ab.space.s), kind = ButtonKind.Quiet,
                )
            }
        }
    }
}

/**
 * A release's icon: a cell per analysed track in the colour of the context it sounds closest to, hollow as none of
 * them is yours yet (grey cells before it's analysed), with a play mark over it; tapping it plays it in Tidal.
 */
@Composable
private fun ReleaseCover(item: ListItem, onPlay: () -> Unit) {
    Box(
        Modifier.size(52.dp).clip(Ab.shapes.small).clickable(onClick = onPlay).semantics { contentDescription = "Play ${item.title} in Tidal" },
        contentAlignment = Alignment.Center,
    ) {
        ListSwatch(item.hues.map { TrackHue(it, near = true) }, size = 52.dp)
        Box(Modifier.size(26.dp).clip(CircleShape).background(Ab.colors.surface.copy(alpha = 0.85f)), contentAlignment = Alignment.Center) {
            AbIcon(AbIcons.Play, tint = Ab.colors.text, size = 14.dp)
        }
    }
}

/** Save and Skip, or what was decided with Undo. Skipping is the queue's "dismissed"; Undo puts it back as new. */
@Composable
private fun Decision(item: ListItem, local: String?, onDecide: (ListItem, String) -> Unit) {
    when (local ?: item.state) {
        "saved" -> Undo("Saved", Ab.colors.keep) { onDecide(item, "new") }
        "dismissed" -> Undo("Skipped", Ab.colors.textMuted) { onDecide(item, "new") }
        else -> Row(horizontalArrangement = Arrangement.spacedBy(Ab.space.xs)) {
            AbButton("Save", { onDecide(item, "saved") }, kind = ButtonKind.Keep, icon = AbIcons.Keep)
            AbButton("Skip", { onDecide(item, "dismissed") }, kind = ButtonKind.Text)
        }
    }
}

@Composable
private fun Undo(label: String, color: androidx.compose.ui.graphics.Color, onUndo: () -> Unit) {
    Row(verticalAlignment = Alignment.CenterVertically) {
        AbText(label, style = Ab.type.number, color = color)
        AbText("Undo", Modifier.clickable(onClick = onUndo).heightIn(min = 44.dp).padding(start = Ab.space.s, top = Ab.space.m), style = Ab.type.caption, color = Ab.colors.textMuted)
    }
}

/** An artist to try: their name and the artists of yours they're closest to, as chips. */
@Composable
fun CloseToRow(item: ListItem, onOpen: () -> Unit) {
    Column(Modifier.fillMaxWidth().clickable(onClick = onOpen).padding(vertical = Ab.space.m), verticalArrangement = Arrangement.spacedBy(Ab.space.xs)) {
        AbText(item.title, style = Ab.type.heading, maxLines = 1)
        if (item.closeTo.isEmpty()) AbText(item.reason, style = Ab.type.caption, color = Ab.colors.textMuted, maxLines = 2)
        else FlowRow(horizontalArrangement = Arrangement.spacedBy(Ab.space.xs), verticalArrangement = Arrangement.spacedBy(Ab.space.xs)) {
            AbText("Close to", Modifier.padding(vertical = 3.dp), style = Ab.type.caption, color = Ab.colors.textMuted)
            (item.closeTo + listOfNotNull(item.closeToMore.takeIf { it > 0 }?.let { "+$it" })).forEach { name ->
                AbText(name, Modifier.clip(Ab.shapes.pill).background(Ab.colors.raised).padding(horizontal = Ab.space.s, vertical = 3.dp), style = Ab.type.caption, maxLines = 1)
            }
        }
    }
}

/** An artist to come back to: how much and when, with a bar per year, their biggest year and this one lit. */
@Composable
fun ThenAndNowRow(item: ListItem, onOpen: () -> Unit) {
    Column(Modifier.fillMaxWidth().clickable(onClick = onOpen).padding(vertical = Ab.space.m), verticalArrangement = Arrangement.spacedBy(Ab.space.xs)) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            AbText(item.title, Modifier.weight(1f), style = Ab.type.heading, maxLines = 1)
            item.plays?.let { AbText("${count(it)} plays", style = Ab.type.number, color = Ab.colors.textMuted) }
        }
        if (item.years.isNotEmpty()) YearBars(yearBars(item.years), "${item.title}: ${item.detail}, ${item.reason}")
        AbText(listOf(item.span.takeIf { it.isNotBlank() }?.let { "Mostly $it" }, item.reason).filterNotNull().joinToString(" · "), style = Ab.type.caption, color = Ab.colors.textMuted, maxLines = 1)
    }
}

@Composable
fun YearBars(bars: List<YearBar>, description: String, modifier: Modifier = Modifier) {
    val peak = Ab.colors.ageEra
    val now = Ab.colors.ageNow
    val rest = Ab.colors.textFaint.copy(alpha = 0.6f)
    Canvas(modifier.fillMaxWidth().height(30.dp).semantics { contentDescription = description }) {
        val max = bars.maxOfOrNull { it.plays }?.takeIf { it > 0 } ?: return@Canvas
        val gap = 3.dp.toPx()
        val w = (size.width - gap * (bars.size - 1)) / bars.size
        bars.forEachIndexed { i, b ->
            val h = (b.plays.toFloat() / max * size.height).coerceAtLeast(2.dp.toPx())
            drawRect(if (b.peak) peak else if (b.now) now else rest, Offset(i * (w + gap), size.height - h), Size(w, h))
        }
    }
}

/** A faded favourite's story as a line: when you played it most, and when you last did. */
@Composable
fun ThenLine(then: Then, modifier: Modifier = Modifier) {
    val at = thenPositions(then) ?: return
    val peak = Ab.colors.ageEra
    val line = Ab.colors.line
    val last = Ab.colors.textMuted
    val description = "${then.peak} plays around ${monthLabel(then.peakAt)}; last played ${monthLabel(then.lastAt)}"
    Column(modifier.fillMaxWidth(), verticalArrangement = Arrangement.spacedBy(Ab.space.xxs)) {
        Canvas(Modifier.fillMaxWidth().height(14.dp).semantics { contentDescription = description }) {
            val y = size.height / 2
            drawLine(line, Offset(0f, y), Offset(size.width, y), 2.dp.toPx())
            drawLine(last.copy(alpha = 0.5f), Offset(at.first * size.width, y), Offset(at.second * size.width, y), 2.dp.toPx())
            drawCircle(peak, 6.dp.toPx(), Offset(at.first * size.width, y))
            drawCircle(last, 4.dp.toPx(), Offset(at.second * size.width, y), style = Stroke(2.dp.toPx()))
        }
        Row {
            AbText("${then.peak} plays around ${monthLabel(then.peakAt)}", Modifier.weight(1f), style = Ab.type.number, color = Ab.colors.ageEra, maxLines = 1)
            AbText("last ${monthLabel(then.lastAt)}", style = Ab.type.number, color = Ab.colors.textMuted, maxLines = 1)
        }
    }
}

/** A set: its length and source, a bar for how much of its tracklist you play, and the context it's closest to. */
@Composable
fun SetCard(item: ListItem, onOpen: () -> Unit) {
    Column(Modifier.fillMaxWidth().clickable(onClick = onOpen).padding(vertical = Ab.space.m), verticalArrangement = Arrangement.spacedBy(Ab.space.xxs)) {
        AbText(item.title, style = Ab.type.heading, maxLines = 2)
        AbText(listOf(item.artist, item.detail).filter { it.isNotBlank() }.joinToString(" · "), style = Ab.type.number, color = Ab.colors.textMuted, maxLines = 1)
        val identified = item.identified ?: 0
        if (identified > 0) {
            val yours = item.yours ?: 0
            val label = "$yours of $identified tracks you play"
            Row(verticalAlignment = Alignment.CenterVertically) {
                val fill = Ab.colors.ageNew
                val track = Ab.colors.line
                Canvas(Modifier.weight(1f).height(6.dp).semantics { contentDescription = label }) {
                    drawRect(track)
                    drawRect(fill, size = size.copy(width = size.width * yours / identified))
                }
                Spacer(Modifier.width(Ab.space.s))
                AbText(label, style = Ab.type.number, color = Ab.colors.textMuted)
            }
        }
        if (item.context.isNotBlank()) AbText("Closest to your ${item.context}", style = Ab.type.caption, color = Ab.colors.textMuted, maxLines = 1)
    }
}
