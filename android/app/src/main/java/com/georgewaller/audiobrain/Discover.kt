package com.georgewaller.audiobrain

import androidx.compose.foundation.Canvas
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
import com.georgewaller.audiobrain.data.Then
import com.georgewaller.audiobrain.design.Ab
import com.georgewaller.audiobrain.design.AbButton
import com.georgewaller.audiobrain.design.AbText
import com.georgewaller.audiobrain.design.AgeTag
import com.georgewaller.audiobrain.design.AgeTagPill
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

/** A Discover section's head: its name in the title style with its tag, why things are in it, and a way to all of it. */
@Composable
fun DiscoverHeader(title: String, caption: String, tag: AgeTag?, all: String?, onAll: (() -> Unit)?) {
    Row(Modifier.fillMaxWidth().padding(top = Ab.space.xl, bottom = Ab.space.xs), verticalAlignment = Alignment.Bottom) {
        Column(Modifier.weight(1f)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                AbText(title, style = Ab.type.title, maxLines = 1)
                if (tag != null) { Spacer(Modifier.width(Ab.space.s)); AgeTagPill(tag) }
            }
            AbText(caption, style = Ab.type.caption, color = Ab.colors.textMuted, maxLines = 2)
        }
        if (all != null && onAll != null) {
            AbText(all, Modifier.clickable(onClick = onAll).heightIn(min = 44.dp).padding(start = Ab.space.s, top = Ab.space.m), style = Ab.type.number, color = Ab.colors.textMuted)
        }
    }
}

/**
 * A release in the queue: what it is, the tracks of yours it sounds like (in full), and Save or Skip. An artist's
 * other waiting releases are folded under it, each decidable too, so twenty from one artist take one row.
 */
@Composable
fun ReleaseCard(item: ListItem, decided: Map<Long, String>, onDecide: (ListItem, String) -> Unit, onOpen: (ListItem) -> Unit, expanded: Boolean, why: Boolean = true, onExpand: () -> Unit) {
    Column(Modifier.fillMaxWidth().padding(vertical = Ab.space.m), verticalArrangement = Arrangement.spacedBy(Ab.space.xxs)) {
        Row(verticalAlignment = Alignment.Top) {
            Column(Modifier.weight(1f).clickable { onOpen(item) }, verticalArrangement = Arrangement.spacedBy(Ab.space.xxs)) {
                AbText(item.title, style = Ab.type.heading, maxLines = 2)
                AbText(item.artist, style = Ab.type.body, color = Ab.colors.textMuted, maxLines = 1)
                AbText(listOf(item.detail, item.why.takeIf { why }.orEmpty()).filter { it.isNotBlank() }.joinToString(" · "), style = Ab.type.number, color = Ab.colors.textMuted, maxLines = 1)
                soundsLike(item.reason)?.let { AbText("Sounds like $it", style = Ab.type.caption, color = Ab.colors.text, maxLines = 3) }
            }
            Spacer(Modifier.width(Ab.space.s))
            Decision(item, decided[item.releaseId], onDecide)
        }
        if (item.othersCount > 0) {
            AbButton(if (expanded) "Fold them away" else "+${item.othersCount} more from ${item.artist}", onExpand, kind = ButtonKind.Quiet)
            if (expanded) {
                Column(Modifier.padding(start = Ab.space.m)) {
                    item.others.forEach { other ->
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
                }
            }
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
            AbButton("Save", { onDecide(item, "saved") }, kind = ButtonKind.Ghost)
            AbButton("Skip", { onDecide(item, "dismissed") }, kind = ButtonKind.Quiet)
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
