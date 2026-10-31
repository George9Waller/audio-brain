package com.georgewaller.audiobrain

import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.runtime.Composable
import androidx.compose.runtime.Stable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import com.georgewaller.audiobrain.data.AlbumRow
import com.georgewaller.audiobrain.data.ApiException
import com.georgewaller.audiobrain.data.ArtistRow
import com.georgewaller.audiobrain.data.HueDto
import com.georgewaller.audiobrain.data.ListItem
import com.georgewaller.audiobrain.data.MapPlace
import com.georgewaller.audiobrain.data.SetRow
import com.georgewaller.audiobrain.data.Verdict
import com.georgewaller.audiobrain.design.Ab
import com.georgewaller.audiobrain.design.AbText
import com.georgewaller.audiobrain.design.AgeTag
import com.georgewaller.audiobrain.design.AgeTagPill
import com.georgewaller.audiobrain.design.AgeTone
import com.georgewaller.audiobrain.design.ListSwatch
import com.georgewaller.audiobrain.design.TrackHue
import com.georgewaller.audiobrain.design.TrackRow
import com.georgewaller.audiobrain.design.color
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Job
import kotlinx.coroutines.launch
import java.time.Instant
import java.time.LocalDate
import java.time.OffsetDateTime
import java.time.Year
import java.time.ZoneId
import java.time.format.DateTimeFormatter
import java.util.Locale

/** Where you can go from any Catalogue page. */
class CatalogueNav(
    val tracks: (recency: String, q: String) -> Unit,
    val language: (code: String) -> Unit,  // tracks by artists who mostly release in it
    val artists: (q: String) -> Unit,
    val albums: (q: String) -> Unit,
    val sets: (q: String) -> Unit,
    val map: () -> Unit,
    val search: (q: String) -> Unit,
    val artist: (Long) -> Unit,
    val album: (Long) -> Unit,
    val set: (Long) -> Unit,
    val context: (Long) -> Unit,
    val track: (Long) -> Unit,
    val list: (String) -> Unit,
    val menu: (ListItem, String) -> Unit,
    val pasteTracklist: () -> Unit,
    val tracklist: (setId: Long, provided: Boolean) -> Unit,
)

// When: the four parts of the library by when you last played a track (server/api/library.py windows)

enum class Recency(val key: String, val label: String, val short: String, val caption: String, val sort: String) {
    Lately("lately", "Played lately", "lately", "Played in the last 45 days", "Most played lately"),
    Year("year", "Earlier this year", "this year", "Played this year, not lately", "Last played"),
    Older("older", "Before this year", "older", "Last played before this year", "Most played then"),
    Never("never", "Never played", "never played", "In the library, never played: each in the colour of what it sounds like", "Closest to your taste"),
}

@Composable
fun Recency.color(): Color = when (this) {
    Recency.Lately -> Ab.colors.ageNow
    Recency.Year -> Ab.colors.ageLately
    Recency.Older -> Ab.colors.ageEra
    Recency.Never -> Ab.colors.ageNew
}

/**
 * The library split by when, as one bar of four parts sized by how many tracks each has (a sliver at least, so a
 * small part can still be seen and tapped); [selected] at full strength and the rest dimmed when one is chosen.
 */
@Composable
fun RecencyBar(counts: Map<String, Int>, modifier: Modifier = Modifier, height: Dp = 10.dp, selected: Recency? = null, onPick: ((Recency) -> Unit)? = null) {
    val total = Recency.entries.sumOf { counts[it.key] ?: 0 }.coerceAtLeast(1)
    val description = Recency.entries.joinToString(", ") { "${count(counts[it.key] ?: 0)} ${it.short}" }
    Row(modifier.fillMaxWidth().height(height).clip(Ab.shapes.small).semantics { contentDescription = "$total tracks: $description" }, horizontalArrangement = Arrangement.spacedBy(2.dp)) {
        Recency.entries.forEach { r ->
            val n = counts[r.key] ?: 0
            Box(
                Modifier.weight((n.toFloat() / total).coerceAtLeast(0.025f)).height(height)
                    .background(r.color().copy(alpha = if (selected == null || selected == r) 1f else 0.4f))
                    .then(if (onPick != null) Modifier.clickable { onPick(r) } else Modifier),
            )
        }
    }
}

/** Under a [RecencyBar]: each part's count and name with its colour. */
@Composable
fun RecencyKey(counts: Map<String, Int>) {
    androidx.compose.foundation.layout.FlowRow(Modifier.padding(top = Ab.space.s), horizontalArrangement = Arrangement.spacedBy(Ab.space.m), verticalArrangement = Arrangement.spacedBy(Ab.space.xs)) {
        Recency.entries.forEach { r ->
            Row(verticalAlignment = Alignment.CenterVertically) {
                Box(Modifier.size(7.dp).clip(CircleShape).background(r.color()))
                Spacer(Modifier.width(Ab.space.xs))
                AbText("${count(counts[r.key] ?: 0)} ${r.short}", style = Ab.type.number, color = Ab.colors.textMuted, maxLines = 1)
            }
        }
    }
}

// Age tags: the same tones as Lists' (Now, Lately, an era's year, New)

/** A track's tag by when you last played it: Now, Lately, its peak year, or New. */
fun ListItem.ageTag(): AgeTag? = when (age) {
    "lately" -> AgeTag("Now", AgeTone.Now)
    "year" -> AgeTag("Lately", AgeTone.Lately)
    "older" -> AgeTag((peak ?: lastPlayed)?.take(4) ?: "Older", AgeTone.Era)
    "never" -> AgeTag("New", AgeTone.New)
    else -> null
}

/** An artist's tag: Now if this year's plays are at least half their biggest year's, else that year. */
fun artistTag(years: Map<String, Int>, now: Int = Year.now().value): AgeTag? {
    val peak = years.maxByOrNull { it.value }?.takeIf { it.value > 0 } ?: return null
    val thisYear = years[now.toString()] ?: 0
    return if (thisYear * 2 >= peak.value) AgeTag("Now", AgeTone.Now) else AgeTag(peak.key, AgeTone.Era)
}

/** An album's or set's tag from its plays: Now (played lately), Lately (this year), the year last played, or New. */
fun playedTag(recent: Int, lastPlayed: String?, now: LocalDate = LocalDate.now()): AgeTag? {
    val last = lastPlayed?.let(::localDate) ?: return AgeTag("New", AgeTone.New)
    return when {
        recent > 0 -> AgeTag("Now", AgeTone.Now)
        last.year == now.year -> AgeTag("Lately", AgeTone.Lately)
        else -> AgeTag("${last.year}", AgeTone.Era)
    }
}

/** A context's tag: Now for the most played lately, Lately for the rest played lately, else its era, or Retired. */
fun contextTag(active: Boolean, rank: Int?, recent: Int, eraStart: String?, eraEnd: String?): AgeTag = when {
    !active -> AgeTag("Retired", AgeTone.Retired)
    rank != null && rank < PLAYING_NOW && recent > 0 -> AgeTag("Now", AgeTone.Now)
    recent > 0 -> AgeTag("Lately", AgeTone.Lately)
    else -> AgeTag(eraSpan(eraStart, eraEnd) ?: "Era", AgeTone.Era)
}

/** Tags for the contexts on the map: ranked by plays lately among them all. */
fun contextTags(places: List<MapPlace>): Map<String, AgeTag> {
    val ranked = places.filter { it.recent > 0 }.sortedByDescending { it.recent }.map { it.key }
    return places.associate { p -> p.key to contextTag(true, ranked.indexOf(p.key).takeIf { it >= 0 }, p.recent, p.eraStart, p.eraEnd) }
}

fun HueDto.track() = TrackHue(hue, near)

fun localDate(text: String): LocalDate? = runCatching { OffsetDateTime.parse(text).atZoneSameInstant(ZoneId.systemDefault()).toLocalDate() }.getOrNull()
    ?: runCatching { LocalDate.parse(text.take(10)) }.getOrNull()

// English month names ("Sep", not the UK locale's "Sept"), as the server's lines have them
private val DAY = DateTimeFormatter.ofPattern("d MMM", Locale.ENGLISH)
private val DAY_YEAR = DateTimeFormatter.ofPattern("d MMM yyyy", Locale.ENGLISH)

/** "20 Oct" this year, "1 Oct 2025" before. */
fun dayLabel(date: LocalDate, now: LocalDate = LocalDate.now()): String = date.format(if (date.year == now.year) DAY else DAY_YEAR)

// Rows

/** A track in the library: its colour icon, title, artist, how much and when, and its age tag; swipe for verdicts. */
@Composable
fun LibraryTrackRow(item: ListItem, verdict: Verdict?, onVerdict: (Verdict) -> Unit, onOpen: () -> Unit, onMenu: () -> Unit) {
    TrackRow(
        item.title, item.artist, detail = item.reason.ifBlank { null },
        verdict = (verdict ?: Verdict.fromApi(item.verdict))?.style(),
        onClick = onOpen, onLongClick = onMenu, onSwipeRight = { onVerdict(Verdict.Keep) }, onSwipeLeft = { onVerdict(Verdict.No) },
        leading = { ListSwatch(listOf(item.hue?.let { TrackHue(it, item.hueNear) }), size = 30.dp) },
        trailing = item.ageTag()?.let { tag -> { AgeTagPill(tag) } },
    )
}

/** An artist: name and tag, plays this year and in all, and a bar per year with their biggest year and this one lit. */
@Composable
fun ArtistLine(row: ArtistRow, onOpen: () -> Unit) {
    Row(Modifier.fillMaxWidth().clickable(onClick = onOpen).heightIn(min = 56.dp).padding(vertical = Ab.space.s), verticalAlignment = Alignment.CenterVertically) {
        Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(Ab.space.xxs)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                AbText(row.name, Modifier.weight(1f, fill = false), style = Ab.type.label, maxLines = 1)
                artistTag(row.years)?.let { Spacer(Modifier.width(Ab.space.s)); AgeTagPill(it) }
            }
            AbText("${count(row.thisYear)} this year · ${count(row.listens)} in all", style = Ab.type.number, color = Ab.colors.textMuted, maxLines = 1)
        }
        Spacer(Modifier.width(Ab.space.m))
        val bars = yearBars(row.years)
        YearBars(bars, "${row.name}: ${count(row.thisYear)} plays this year, most in ${bars.firstOrNull { it.peak }?.year ?: "none"}", Modifier.width(110.dp), gap = 2.dp)
    }
}

/** An album: its colour icon filling in as more of it is played, its tag, and a bar for how much of it you've played. */
@Composable
fun AlbumLine(row: AlbumRow, onOpen: () -> Unit) {
    Row(Modifier.fillMaxWidth().clickable(onClick = onOpen).padding(vertical = Ab.space.m), verticalAlignment = Alignment.CenterVertically) {
        ListSwatch(row.hues.map { it.track() }.ifEmpty { listOf(null) }, size = 48.dp)
        Spacer(Modifier.width(Ab.space.m))
        Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(Ab.space.xxs)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                AbText(row.name, Modifier.weight(1f), style = Ab.type.label, maxLines = 1)
                playedTag(row.recent, row.lastPlayed)?.let { Spacer(Modifier.width(Ab.space.s)); AgeTagPill(it) }
            }
            AbText(row.artist, style = Ab.type.caption, color = Ab.colors.textMuted, maxLines = 1)
            Row(verticalAlignment = Alignment.CenterVertically) {
                Share(row.played, row.tracks, "${row.played} of ${row.tracks} tracks played", Modifier.weight(1f))
                Spacer(Modifier.width(Ab.space.s))
                AbText("${row.played}/${row.tracks} · ${count(row.listens)} plays", style = Ab.type.number, color = Ab.colors.textMuted, maxLines = 1)
            }
        }
    }
}

/** A thin bar for a share: [part] of [whole]. */
@Composable
fun Share(part: Int, whole: Int, description: String, modifier: Modifier = Modifier, height: Dp = 4.dp, color: Color = Ab.colors.textMuted) {
    val track = Ab.colors.line
    Canvas(modifier.height(height).clip(Ab.shapes.pill).semantics { contentDescription = description }) {
        drawRect(track)
        if (whole > 0) drawRect(color, size = size.copy(width = size.width * part / whole))
    }
}

/** A set: title and tag, its length and when, then a cell per tracklist line: yours lit, in the catalogue dim, unknown empty. */
@Composable
fun SetLine(row: SetRow, onOpen: () -> Unit) {
    Column(Modifier.fillMaxWidth().clickable(onClick = onOpen).padding(vertical = Ab.space.m), verticalArrangement = Arrangement.spacedBy(Ab.space.xs)) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            AbText(row.title, Modifier.weight(1f), style = Ab.type.label, maxLines = 2)
            setTag(row)?.let { Spacer(Modifier.width(Ab.space.s)); AgeTagPill(it) }
        }
        AbText(setMeta(row).uppercase(), style = Ab.type.number, color = Ab.colors.textMuted, maxLines = 1)
        if (row.strip.isEmpty()) AbText(if (row.hits > 0) "${row.hits} matching tracks in the tracklist" else "No tracklist yet", style = Ab.type.number, color = Ab.colors.textFaint)
        else Row(verticalAlignment = Alignment.CenterVertically) {
            TracklistStrip(row.strip, Modifier.weight(1f))
            Spacer(Modifier.width(Ab.space.s))
            AbText(if (row.hits > 0) "${row.hits} matching in the tracklist" else "${row.yours} of ${row.strip.size} you play", style = Ab.type.number, color = Ab.colors.textMuted, maxLines = 1)
        }
    }
}

fun setTag(row: SetRow): AgeTag? {
    val last = row.lastPlayed?.let(::localDate)
    return when {
        last != null && last.isAfter(LocalDate.now().minusDays(45)) -> AgeTag("Now", AgeTone.Now)
        row.plays == 0 && row.source in setOf("saved", "provided") -> AgeTag("New", AgeTone.New)
        else -> null
    }
}

/** "98 min · 10 plays · last 20 Oct", "109 min · saved 26 Oct · not played", "116 min · 1 Oct 2025 · Mayan Warrior". */
fun setMeta(row: SetRow): String = listOfNotNull(
    row.minutes?.let { "$it min" },
    when (row.source) {
        "played" -> "${row.plays} ${if (row.plays == 1) "play" else "plays"}"
        "saved", "provided" -> row.savedAt?.let(::localDate)?.let { "saved ${dayLabel(it)}" }
        else -> row.published?.let(::localDate)?.let(::dayLabel)
    },
    when {
        row.source == "new" -> row.by.ifBlank { null }
        row.lastPlayed != null -> row.lastPlayed?.let(::localDate)?.let { "last ${dayLabel(it)}" }
        else -> "not played"
    },
).joinToString(" · ")

@Composable
fun TracklistStrip(strip: List<Int>, modifier: Modifier = Modifier) {
    val yours = Ab.colors.ageNow
    val known = Ab.colors.textFaint
    val unknown = Ab.colors.raised
    val yoursCount = strip.count { it == 2 }
    val knownCount = strip.count { it == 1 }
    Canvas(modifier.height(8.dp).semantics { contentDescription = "Tracklist of ${strip.size}: $yoursCount you've played, $knownCount more in your catalogue" }) {
        val gap = (if (strip.size > 60) 1 else 2).dp.toPx()
        val w = ((size.width - gap * (strip.size - 1)) / strip.size).coerceAtLeast(1f)
        strip.forEachIndexed { i, s -> drawRect(when (s) { 2 -> yours; 1 -> known; else -> unknown }, Offset(i * (w + gap), 0f), Size(w, size.height)) }
    }
}

/** A dot in a track's context colour; hollow when it only sounds like it, or grey when it has no colour. */
@Composable
fun HueDot(hue: Float?, near: Boolean, size: Dp = 10.dp) {
    val colour = if (hue == null) Ab.colors.textFaint else com.georgewaller.audiobrain.design.contextColor(hue)
    Canvas(Modifier.size(size)) {
        if (near || hue == null) drawCircle(colour, radius = this.size.minDimension / 2 - 0.75.dp.toPx(), style = Stroke(1.5.dp.toPx()))
        else drawCircle(colour)
    }
}

/** Bars for plays over time (months or years), [highlight] in the Now colour, the rest in [color]. */
@Composable
fun PlayBars(values: List<Int>, color: Color, description: String, modifier: Modifier = Modifier, height: Dp = 64.dp, highlight: Int? = values.lastIndex) {
    val now = Ab.colors.ageNow
    val empty = Ab.colors.line
    Canvas(modifier.fillMaxWidth().height(height).semantics { contentDescription = description }) {
        val max = values.maxOrNull()?.takeIf { it > 0 } ?: return@Canvas
        val gap = (if (values.size > 24) 2 else 4).dp.toPx()
        val w = (size.width - gap * (values.size - 1)) / values.size
        values.forEachIndexed { i, v ->
            val h = (v.toFloat() / max * size.height).coerceAtLeast(2.dp.toPx())
            drawRect(if (v == 0) empty else if (i == highlight) now else color, Offset(i * (w + gap), size.height - h), Size(w, h))
        }
    }
}

/** A box the size of a tag-coloured tile: label and count (the Library home's doors, the Tracks page's parts). */
@Composable
fun Tile(title: String, detail: String, modifier: Modifier = Modifier, dot: Color? = null, selected: Boolean = false, onClick: () -> Unit) {
    Column(
        modifier.clip(Ab.shapes.medium).background(if (selected) Ab.colors.raised else Ab.colors.surface)
            .border(1.dp, if (selected) Ab.colors.text else Ab.colors.line, Ab.shapes.medium)
            .clickable(onClick = onClick).heightIn(min = 60.dp).padding(horizontal = Ab.space.m, vertical = Ab.space.s),
        verticalArrangement = Arrangement.spacedBy(Ab.space.xxs),
    ) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            if (dot != null) { Box(Modifier.size(8.dp).clip(CircleShape).background(dot)); Spacer(Modifier.width(Ab.space.s)) }
            AbText(title, style = Ab.type.label, maxLines = 1)
        }
        AbText(detail, style = Ab.type.number, color = Ab.colors.textMuted, maxLines = 1)
    }
}

/** Part of a card made of lazy rows: the first row rounds its top, the last its bottom. */
@Composable
fun Modifier.cardSlice(first: Boolean, last: Boolean): Modifier {
    val r = 18.dp
    val shape = RoundedCornerShape(topStart = if (first) r else 0.dp, topEnd = if (first) r else 0.dp, bottomStart = if (last) r else 0.dp, bottomEnd = if (last) r else 0.dp)
    return this.clip(shape).background(Ab.colors.surface).padding(horizontal = Ab.space.m)
}

/**
 * Pages of a list loaded as you scroll: [load] takes an offset and gives the next page. Kept by the screen that
 * shows it, so it lasts as long as the screen does.
 */
@Stable
class Paged<T>(private val scope: CoroutineScope, private val pageSize: Int = 50, private val load: suspend (offset: Int) -> List<T>) {
    var items by mutableStateOf<List<T>>(emptyList()); private set
    var loading by mutableStateOf(false); private set
    var end by mutableStateOf(false); private set
    var error by mutableStateOf<String?>(null); private set
    private var job: Job? = null

    init { more() }

    /** After a failure: try the next page again. */
    fun retry() { error = null; end = false; more() }

    fun more() {
        if (loading || end) return
        loading = true
        job = scope.launch {
            try {
                val page = load(items.size)
                items = items + page
                end = page.size < pageSize
                error = null
            } catch (e: ApiException) {
                error = e.message; end = true
            }
            loading = false
        }
    }
}

/** Plays per month this year so far, as "Jan".."Oct" labels. */
fun monthLabels(count: Int): List<String> = (1..count).map { java.time.Month.of(it).getDisplayName(java.time.format.TextStyle.SHORT, Locale.ENGLISH) }

/** The month most of [months] (this year, January first) have come since, when most came in the last few: "since August". */
fun sinceMonth(months: List<Int>): String? {
    val total = months.sum().takeIf { it > 0 } ?: return null
    var run = 0
    for (i in months.indices.reversed()) {
        run += months[i]
        if (run * 2 >= total) return if (i == 0) null else java.time.Month.of(i + 1).getDisplayName(java.time.format.TextStyle.FULL, Locale.ENGLISH)
    }
    return null
}

fun instantOf(text: String?): Instant? = text?.let { runCatching { OffsetDateTime.parse(it).toInstant() }.getOrNull() }
