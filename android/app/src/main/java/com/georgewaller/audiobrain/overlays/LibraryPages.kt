package com.georgewaller.audiobrain.overlays

import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ColumnScope
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.LazyRow
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.PointMode
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.georgewaller.audiobrain.AlbumLine
import com.georgewaller.audiobrain.AppViewModel
import com.georgewaller.audiobrain.ArtistLine
import com.georgewaller.audiobrain.CardRow
import com.georgewaller.audiobrain.CatalogueNav
import com.georgewaller.audiobrain.DiscoverSection
import com.georgewaller.audiobrain.FIRST_YEAR
import com.georgewaller.audiobrain.HueDot
import com.georgewaller.audiobrain.LibraryTrackRow
import com.georgewaller.audiobrain.ListAge
import com.georgewaller.audiobrain.PlayBars
import com.georgewaller.audiobrain.SectionHead
import com.georgewaller.audiobrain.SetLine
import com.georgewaller.audiobrain.Share
import com.georgewaller.audiobrain.YearBars
import com.georgewaller.audiobrain.ageTag
import com.georgewaller.audiobrain.artistTag
import com.georgewaller.audiobrain.byAge
import com.georgewaller.audiobrain.contextTag
import com.georgewaller.audiobrain.count
import com.georgewaller.audiobrain.data.AlbumPage
import com.georgewaller.audiobrain.data.ApiException
import com.georgewaller.audiobrain.data.ArtistPage
import com.georgewaller.audiobrain.data.ContextPage
import com.georgewaller.audiobrain.data.DataGraph
import com.georgewaller.audiobrain.data.LibrarySearch
import com.georgewaller.audiobrain.data.ListItem
import com.georgewaller.audiobrain.data.SetPage
import com.georgewaller.audiobrain.data.TrackStory
import com.georgewaller.audiobrain.data.Verdict
import com.georgewaller.audiobrain.dayLabel
import com.georgewaller.audiobrain.design.Ab
import com.georgewaller.audiobrain.design.AbButton
import com.georgewaller.audiobrain.design.AbChip
import com.georgewaller.audiobrain.design.AbIcons
import com.georgewaller.audiobrain.design.AbText
import com.georgewaller.audiobrain.design.AbTextField
import com.georgewaller.audiobrain.design.AgeTag
import com.georgewaller.audiobrain.design.AgeTagPill
import com.georgewaller.audiobrain.design.AgeTone
import com.georgewaller.audiobrain.design.ButtonKind
import com.georgewaller.audiobrain.design.EmptyState
import com.georgewaller.audiobrain.design.HourBars
import com.georgewaller.audiobrain.design.ListSwatch
import com.georgewaller.audiobrain.design.Overline
import com.georgewaller.audiobrain.design.SoundSigil
import com.georgewaller.audiobrain.design.Spinner
import com.georgewaller.audiobrain.design.TrackRow
import com.georgewaller.audiobrain.design.VerdictBar
import com.georgewaller.audiobrain.design.contextColor
import com.georgewaller.audiobrain.eraSpan
import com.georgewaller.audiobrain.localDate
import com.georgewaller.audiobrain.monthLabel
import com.georgewaller.audiobrain.monthLabels
import com.georgewaller.audiobrain.openUrl
import com.georgewaller.audiobrain.playedTag
import com.georgewaller.audiobrain.shortName
import com.georgewaller.audiobrain.sigil
import com.georgewaller.audiobrain.sinceMonth
import com.georgewaller.audiobrain.style
import com.georgewaller.audiobrain.tag
import com.georgewaller.audiobrain.track
import com.georgewaller.audiobrain.verdict
import com.georgewaller.audiobrain.yearBars
import kotlinx.coroutines.launch
import java.time.LocalDate
import java.time.Year
import java.time.YearMonth
import java.time.format.DateTimeFormatter
import java.util.Locale
import kotlin.math.roundToInt

/** Loads a page once, showing a placeholder until it arrives. */
@Composable
fun <T> Loaded(key: Any, load: suspend () -> T, content: @Composable (T) -> Unit) {
    var value by remember(key) { mutableStateOf<T?>(null) }
    var error by remember(key) { mutableStateOf<String?>(null) }
    LaunchedEffect(key) { try { value = load() } catch (e: ApiException) { error = e.message } }
    val v = value
    if (v == null) {
        if (error != null) EmptyState("Couldn't load it", error.orEmpty())
        else Box(Modifier.fillMaxWidth().padding(top = Ab.space.xxl), contentAlignment = Alignment.Center) { Spinner() }
    } else content(v)
}

/** The facts line under a page's title, in mono. */
@Composable
private fun Facts(text: String, modifier: Modifier = Modifier) = AbText(text, modifier, style = Ab.type.number, color = Ab.colors.textMuted, maxLines = 2)

@Composable
private fun Caption(text: String, modifier: Modifier = Modifier) = AbText(text, modifier, style = Ab.type.caption, color = Ab.colors.textMuted, maxLines = 3)

@Composable
private fun Title(text: String, tag: AgeTag?) {
    Row(verticalAlignment = Alignment.CenterVertically) {
        AbText(text, Modifier.weight(1f, fill = false), style = Ab.type.display, maxLines = 3)
        tag?.let { Spacer(Modifier.width(Ab.space.s)); AgeTagPill(it) }
    }
}

/** A chip for a context: its colour, its name, and a count. */
@Composable
private fun ContextChip(name: String, hue: Float, n: Int?, onClick: () -> Unit) {
    Row(
        Modifier.clip(Ab.shapes.pill).background(Ab.colors.raised).clickable(onClick = onClick).heightIn(min = 40.dp).padding(horizontal = Ab.space.m),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        HueDot(hue, near = false, size = 8.dp)
        Spacer(Modifier.width(Ab.space.s))
        AbText(shortName(name), style = Ab.type.caption, maxLines = 1)
        n?.let { AbText("  $it", style = Ab.type.number, color = Ab.colors.textMuted) }
    }
}

/** A card with one row that leads somewhere: a tag or dot, what, and an arrow. */
@Composable
private fun DoorCard(onClick: () -> Unit, content: @Composable () -> Unit) {
    Row(
        Modifier.fillMaxWidth().clip(Ab.shapes.medium).background(Ab.colors.surface).border(1.dp, Ab.colors.line, Ab.shapes.medium)
            .clickable(onClick = onClick).heightIn(min = 52.dp).padding(horizontal = Ab.space.m),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Box(Modifier.weight(1f)) { content() }
        AbText("→", style = Ab.type.label, color = Ab.colors.textMuted)
    }
}

/** Year labels under a bar per year: every other year, short. */
@Composable
private fun YearAxis(from: Int, to: Int) {
    Row(Modifier.fillMaxWidth().padding(top = Ab.space.xs)) {
        (from..to).forEach { y -> AbText(if ((y - from) % 2 == 0) "’${y % 100}" else "", Modifier.weight(1f), style = Ab.type.number, color = Ab.colors.textMuted, maxLines = 1) }
    }
}

// An artist (C)

@Composable
fun ArtistScreen(vm: AppViewModel, graph: DataGraph, id: Long, padding: PaddingValues, nav: CatalogueNav) {
    val verdicts by vm.verdicts.collectAsStateWithLifecycle()
    val context = LocalContext.current
    Loaded<ArtistPage>(id, { graph.api.artistPage(id) }) { page ->
        val a = page.artist
        val bars = yearBars(a.years)
        LazyColumn(Modifier.fillMaxSize(), contentPadding = padding) {
            item(key = "h") {
                Column(verticalArrangement = Arrangement.spacedBy(Ab.space.s)) {
                    Title(a.name, artistTag(a.years))
                    Facts("${count(a.listens)} plays · ${count(page.catalogue)} tracks · ${count(page.never)} never played")
                    YearBars(bars, "Plays per year: ${bars.filter { it.plays > 0 }.joinToString { "${it.plays} in ${it.year}" }}", Modifier.padding(top = Ab.space.m), height = 64.dp, gap = 4.dp)
                    YearAxis(FIRST_YEAR, Year.now().value)
                    Caption(yearsLine(a.years))
                    Row(horizontalArrangement = Arrangement.spacedBy(Ab.space.s)) {
                        page.tracks.firstOrNull()?.let { t -> AbButton("Radio", { vm.startRadio(t) }, kind = ButtonKind.Quiet) }
                        AbButton("Explore", { vm.explore(name = a.name, artistId = a.id) }, kind = ButtonKind.Quiet)
                        page.tidalUrl?.let { url -> AbButton("Tidal", { openUrl(context, url) }, kind = ButtonKind.Ghost, icon = AbIcons.Open) }
                    }
                    if (page.waiting > 0) DoorCard({ nav.list("releases") }) {
                        Row(verticalAlignment = Alignment.CenterVertically) {
                            AgeTagPill(AgeTag("New", AgeTone.New))
                            Spacer(Modifier.width(Ab.space.m))
                            AbText("${page.waiting} new ${if (page.waiting == 1) "release" else "releases"} waiting in Discover", style = Ab.type.label)
                        }
                    }
                }
            }
            if (page.contexts.isNotEmpty()) item(key = "ctx") {
                Column(Modifier.padding(top = Ab.space.xl)) {
                    SectionHead("", Ab.colors.textMuted, "In your contexts", "Their tracks sit in ${page.contextCount} of your contexts; the most:")
                    FlowRow(horizontalArrangement = Arrangement.spacedBy(Ab.space.s), verticalArrangement = Arrangement.spacedBy(Ab.space.s)) {
                        page.contexts.forEach { c -> ContextChip(c.name, c.hue, c.tracks) { nav.context(c.id) } }
                        if (page.contextCount > page.contexts.size) AbText("+${page.contextCount - page.contexts.size} more", Modifier.padding(Ab.space.s), style = Ab.type.caption, color = Ab.colors.textMuted)
                    }
                }
            }
            if (page.tracks.isNotEmpty()) item(key = "tracks") {
                DiscoverSection("${page.tracks.size} played", Ab.colors.textMuted, "Tracks", "Most played first", emptyList()) {
                    page.tracks.forEach { t -> CardRow { LibraryTrackRow(t, verdicts[t.ref.key], { v -> vm.give(v, t, "artist") }, { t.openWith(nav, "artist") }, { nav.menu(t, "artist") }) } }
                }
            }
            if (page.unplayed.isNotEmpty()) item(key = "unplayed") {
                DiscoverSection("New · ${count(page.never)} tracks", Ab.colors.ageNew, "Not played yet", "Closest to your contexts first", emptyList()) {
                    page.unplayed.forEach { t -> CardRow { LibraryTrackRow(t, verdicts[t.ref.key], { v -> vm.give(v, t, "artist") }, { t.openWith(nav, "artist") }, { nav.menu(t, "artist") }) } }
                }
            }
            if (page.albums.isNotEmpty()) item(key = "albums") {
                DiscoverSection("${page.albums.size} albums", Ab.colors.textMuted, "Albums", "Most played first", emptyList()) {
                    page.albums.forEach { al -> CardRow { AlbumLine(al) { nav.album(al.id) } } }
                }
            }
        }
    }
}

/** "Biggest in 2022 and 2024 (238 plays each); 156 so far this year." */
fun yearsLine(years: Map<String, Int>, now: Int = Year.now().value): String {
    val top = years.values.maxOrNull()?.takeIf { it > 0 } ?: return "Not played yet"
    val peaks = years.filter { it.value == top }.keys.sorted()
    val biggest = if (peaks.size == 1) "Biggest in ${peaks[0]} (${count(top)} plays)" else "Biggest in ${peaks.dropLast(1).joinToString()} and ${peaks.last()} (${count(top)} plays each)"
    val thisYear = years[now.toString()] ?: 0
    return if (peaks == listOf(now.toString())) "Biggest this year: ${count(top)} plays so far." else "$biggest; ${count(thisYear)} so far this year."
}

// An album

@Composable
fun AlbumScreen(vm: AppViewModel, graph: DataGraph, id: Long, padding: PaddingValues, nav: CatalogueNav) {
    val verdicts by vm.verdicts.collectAsStateWithLifecycle()
    val context = LocalContext.current
    Loaded<AlbumPage>(id, { graph.api.albumPage(id) }) { page ->
        val a = page.album
        LazyColumn(Modifier.fillMaxSize(), contentPadding = padding) {
            item(key = "h") {
                Column(verticalArrangement = Arrangement.spacedBy(Ab.space.s)) {
                    Row(verticalAlignment = Alignment.Bottom) {
                        ListSwatch(a.hues.map { it.track() }.ifEmpty { listOf(null) }, size = 96.dp)
                        Spacer(Modifier.width(Ab.space.m))
                        Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(Ab.space.xxs)) {
                            Overline("Album · ${a.artist}", Modifier.clickable { nav.artist(a.artistId) }, color = Ab.colors.textMuted)
                            AbText(a.name, style = Ab.type.display.copy(fontSize = 30.sp, lineHeight = 32.sp), maxLines = 3)
                            playedTag(a.recent, a.lastPlayed)?.let { AgeTagPill(it) }
                        }
                    }
                    Facts("${a.played} of ${a.tracks} played · ${count(a.listens)} plays" + (if (a.recent > 0) " · ${a.recent} lately" else ""), Modifier.padding(top = Ab.space.s))
                    Share(a.played, a.tracks, "${a.played} of ${a.tracks} tracks played", Modifier.fillMaxWidth(), height = 6.dp)
                    val most = page.mostPlayed?.let { "Most played: ${it.title} (${it.plays})." }
                    val home = page.context?.let { "Mostly your ${shortName(it.name)}." }
                    listOfNotNull(most, home).takeIf { it.isNotEmpty() }?.let { Caption(it.joinToString(" ")) }
                    Row(horizontalArrangement = Arrangement.spacedBy(Ab.space.s)) {
                        AbButton("Artist", { nav.artist(a.artistId) }, kind = ButtonKind.Quiet)
                        page.context?.let { c -> AbButton("Its context", { nav.context(c.id) }, kind = ButtonKind.Quiet) }
                        a.tidalUrl?.let { url -> AbButton("Tidal", { openUrl(context, url) }, kind = ButtonKind.Ghost, icon = AbIcons.Open) }
                    }
                }
            }
            val (played, not) = page.tracks.partition { it.plays.orZero() > 0 }
            listOf(Triple("played", "Played", played), Triple("not", "Not played yet", not)).forEach { (key, title, rows) ->
                if (rows.isNotEmpty()) item(key = key) {
                    DiscoverSection(if (key == "not") "New · ${rows.size}" else "${rows.size} of ${page.tracks.size}", if (key == "not") Ab.colors.ageNew else Ab.colors.textMuted, title,
                        if (key == "not") "Hollow in the colour of the context each sounds like" else "In their context's colour; hollow if they're only close to one", emptyList()) {
                        rows.forEach { t -> CardRow { AlbumTrackRow(vm, t, verdicts[t.ref.key], nav) } }
                    }
                }
            }
        }
    }
}

private fun Int?.orZero() = this ?: 0

@Composable
private fun AlbumTrackRow(vm: AppViewModel, t: ListItem, verdict: Verdict?, nav: CatalogueNav) {
    val sound = listOfNotNull(t.sound?.bpm?.roundToInt()?.let { "$it BPM" }, t.key.ifBlank { null }?.let(::keyName)).joinToString(" · ")
    val plays = t.plays.orZero()
    TrackRow(
        t.title, sound, verdict = (verdict ?: Verdict.fromApi(t.verdict))?.style(),
        onClick = { t.openWith(nav, "album") }, onLongClick = { nav.menu(t, "album") },
        onSwipeRight = { vm.give(Verdict.Keep, t, "album") }, onSwipeLeft = { vm.give(Verdict.No, t, "album") },
        leading = { Box(Modifier.size(30.dp), contentAlignment = Alignment.Center) { HueDot(t.hue, t.hueNear) } },
        trailing = {
            AbText(
                if (plays == 0) "not yet" else "$plays" + (t.recent?.takeIf { it > 0 }?.let { " · $it lately" } ?: ""),
                style = Ab.type.number, color = if ((t.recent ?: 0) > 0) Ab.colors.ageNow else Ab.colors.textMuted,
            )
        },
    )
}

// A set

@Composable
fun SetScreen(vm: AppViewModel, graph: DataGraph, id: Long, padding: PaddingValues, nav: CatalogueNav) {
    val context = LocalContext.current
    Loaded<SetPage>(id, { graph.api.setPage(id) }) { page ->
        val s = page.set
        LazyColumn(Modifier.fillMaxSize(), contentPadding = padding) {
            item(key = "h") {
                Column(verticalArrangement = Arrangement.spacedBy(Ab.space.s)) {
                    Overline(listOfNotNull("Set", s.by.ifBlank { null }, (s.published ?: s.savedAt)?.let(::localDate)?.let(::dayLabel)).joinToString(" · "), color = Ab.colors.textMuted)
                    AbText(s.title, style = Ab.type.display.copy(fontSize = 30.sp, lineHeight = 32.sp), maxLines = 4)
                    Facts(listOfNotNull(s.minutes?.let { "$it min" }, if (s.entries > 0) "${s.entries} tracks identified" else "no tracklist yet", if (s.entries > 0) "${s.yours} you play" else null, if (s.plays > 0) "you've played it ${s.plays} ${if (s.plays == 1) "time" else "times"}" else null).joinToString(" · "))
                    page.context?.let { c -> Caption("Closest to your ${shortName(c.name)}", Modifier.clickable { nav.context(c.id) }) }
                    Row(horizontalArrangement = Arrangement.spacedBy(Ab.space.s)) {
                        AbButton(if ("youtube" in s.url) "Play on YouTube" else "Find in Tidal", { openUrl(context, s.url) }, icon = AbIcons.Open)
                        if (s.source == "provided") AbButton("Matches", { nav.tracklist(s.id, true) }, kind = ButtonKind.Quiet)
                        else AbButton(if (page.entries.isEmpty()) "Paste tracklist" else "Replace tracklist", { nav.tracklist(s.id, false) }, kind = ButtonKind.Quiet)
                    }
                }
            }
            val placed = page.entries.filter { it.minute != null }
            if (placed.isNotEmpty()) item(key = "where") {
                Column(Modifier.padding(top = Ab.space.xl)) {
                    SectionHead("", Ab.colors.textMuted, "Where your tracks are", "Across the set, the tracks you play in their context's colour")
                    SetTimeline(page)
                }
            }
            if (page.entries.isNotEmpty()) item(key = "list") {
                DiscoverSection("${page.entries.size} tracks", Ab.colors.textMuted, "Tracklist", "Your tracks in their context's colour with your plays; the rest hollow", emptyList()) {
                    page.entries.forEach { e ->
                        CardRow {
                            val t = e.track
                            val plays = t?.plays.orZero()
                            Row(
                                Modifier.fillMaxWidth().then(if (t != null) Modifier.clickable { t.openWith(nav, "set:$id") } else Modifier).heightIn(min = 52.dp).padding(vertical = Ab.space.xs),
                                verticalAlignment = Alignment.CenterVertically,
                            ) {
                                AbText(e.cue.ifBlank { "${e.position}" }, Modifier.width(52.dp), style = Ab.type.number, color = Ab.colors.textMuted, maxLines = 1)
                                HueDot(if (t != null) t.hue else null, near = plays == 0 || t?.hueNear == true)
                                Spacer(Modifier.width(Ab.space.m))
                                Column(Modifier.weight(1f)) {
                                    AbText(t?.title ?: e.title, style = Ab.type.label, color = if (plays > 0) Ab.colors.text else Ab.colors.textMuted, maxLines = 1)
                                    AbText(t?.artist ?: e.artist, style = Ab.type.caption, color = Ab.colors.textMuted, maxLines = 1)
                                }
                                if (plays > 0) AbText("$plays ${if (plays == 1) "play" else "plays"}", style = Ab.type.number, color = Ab.colors.ageNow)
                            }
                        }
                    }
                }
            }
        }
    }
}

/** The set's length as a line, a mark where each identified track starts: yours tall in their context's colour, the rest short and grey. */
@Composable
private fun SetTimeline(page: SetPage) {
    val length = (page.set.minutes?.toFloat() ?: 0f).coerceAtLeast((page.entries.mapNotNull { it.minute }.maxOrNull() ?: 0f) + 1f)
    val line = Ab.colors.line
    val other = Ab.colors.textFaint
    val marks = page.entries.mapNotNull { e ->
        val m = e.minute ?: return@mapNotNull null
        val yours = (e.track?.plays ?: 0) > 0
        Triple(m / length, yours, if (yours) contextColor(e.track?.hue) else other)
    }
    val yours = page.entries.filter { (it.track?.plays ?: 0) > 0 && it.minute != null }.joinToString { it.cue }
    Column {
        Canvas(Modifier.fillMaxWidth().height(30.dp).semantics { contentDescription = "The set's ${length.roundToInt()} minutes; your tracks at $yours" }) {
            val y = size.height / 2
            drawLine(line, Offset(0f, y), Offset(size.width, y), 2.dp.toPx())
            marks.sortedBy { it.second }.forEach { (at, mine, colour) ->
                val h = (if (mine) 26 else 12).dp.toPx()
                drawLine(colour, Offset(at * size.width, y - h / 2), Offset(at * size.width, y + h / 2), (if (mine) 4 else 2).dp.toPx(), cap = StrokeCap.Round)
            }
        }
        Row(Modifier.fillMaxWidth().padding(top = Ab.space.xs)) {
            val stops = listOf(0, 30, 60, 90, 120, 150, 180).filter { it < length - 10 }
            stops.forEach { m -> AbText(if (m == 0) "0:00" else "$m", Modifier.weight(1f), style = Ab.type.number, color = Ab.colors.textMuted) }
            AbText("${length.roundToInt()} min", style = Ab.type.number, color = Ab.colors.textMuted)
        }
    }
}

// A context (D)

@Composable
fun ContextScreen(vm: AppViewModel, graph: DataGraph, id: Long, padding: PaddingValues, nav: CatalogueNav) {
    val verdicts by vm.verdicts.collectAsStateWithLifecycle()
    Loaded<ContextPage>(id, { graph.api.contextPage(id) }) { page ->
        val colour = contextColor(page.hue)
        LazyColumn(Modifier.fillMaxSize(), contentPadding = padding) {
            item(key = "h") {
                Column(verticalArrangement = Arrangement.spacedBy(Ab.space.s)) {
                    ContextOnMap(graph, page.members.toSet(), page.hue)
                    AbText("On the map: this context lit, the rest dimmed", style = Ab.type.number, color = Ab.colors.textMuted)
                    Row(Modifier.padding(top = Ab.space.m), verticalAlignment = Alignment.CenterVertically) {
                        HueDot(page.hue, near = false, size = 12.dp)
                        Spacer(Modifier.width(Ab.space.s))
                        Overline(page.family.ifBlank { "Context" }, color = Ab.colors.textMuted)
                    }
                    AbText(shortName(page.name), style = Ab.type.display, maxLines = 3)
                    Row(horizontalArrangement = Arrangement.spacedBy(Ab.space.s)) {
                        AgeTagPill(contextTag(page.active, page.rank, page.recent, page.eraStart, page.eraEnd))
                        if (page.synced) InTidal()
                    }
                    Row(Modifier.padding(top = Ab.space.s), horizontalArrangement = Arrangement.spacedBy(Ab.space.s)) {
                        Stat(count(page.size), "tracks", Modifier.weight(1f))
                        Stat(count(page.recent), "plays lately", Modifier.weight(1f))
                        Stat(eraSpan(page.eraStart, page.eraEnd) ?: "—", "its era", Modifier.weight(1f))
                    }
                    if (page.hours.size == 24) {
                        val peak = page.hours.indices.maxBy { page.hours[it] }
                        SectionHead("", Ab.colors.textMuted, "When you play it", "", Modifier.padding(top = Ab.space.l))
                        HourBars(page.hours, colour, peak, "Plays by hour of the day, most around ${"%02d".format(peak)}:00", Modifier.fillMaxWidth().height(48.dp))
                        // under the bar each label names: four quarters of the day
                        Row(Modifier.fillMaxWidth()) {
                            listOf("00", "06", "12", "18").forEach { AbText(it, Modifier.weight(1f), style = Ab.type.number, color = Ab.colors.textMuted) }
                        }
                        Caption("Most around ${"%02d".format(peak)}:00")
                    }
                    if (page.styles.isNotEmpty()) FlowRow(Modifier.padding(top = Ab.space.s), horizontalArrangement = Arrangement.spacedBy(Ab.space.xs), verticalArrangement = Arrangement.spacedBy(Ab.space.xs)) {
                        page.styles.forEach { AbText(it, Modifier.clip(Ab.shapes.pill).background(Ab.colors.raised).padding(horizontal = Ab.space.m, vertical = Ab.space.xs), style = Ab.type.caption, color = Ab.colors.textMuted) }
                    }
                    page.listKey?.let { key -> AbButton("Open its list", { nav.list(key) }, Modifier.padding(top = Ab.space.s), kind = ButtonKind.Quiet) }
                }
            }
            item(key = "tracks") {
                DiscoverSection("${count(page.size)} tracks", colour, "Its tracks", "Core → edge: the ones that define it first, the ones that might belong elsewhere last", emptyList()) {
                    page.tracks.forEach { t ->
                        CardRow {
                            val fit = 1f - ((t.distance ?: 0f) / page.far.coerceAtLeast(0.0001f)).coerceIn(0f, 1f)
                            TrackRow(
                                t.title, t.artist, verdict = (verdicts[t.ref.key] ?: Verdict.fromApi(t.verdict))?.style(),
                                onClick = { t.openWith(nav, "context:$id") }, onLongClick = { nav.menu(t, "context:$id") },
                                onSwipeRight = { vm.give(Verdict.Keep, t, "context:$id") }, onSwipeLeft = { vm.give(Verdict.No, t, "context:$id") },
                                leading = { Share((fit * 100).roundToInt(), 100, "Fit ${(fit * 100).roundToInt()}%", Modifier.width(30.dp), color = colour) },
                                facts = listOfNotNull(t.recent?.takeIf { it > 0 }?.let { "$it lately" }, "${t.plays ?: 0} in all").joinToString("\n"),
                            )
                        }
                    }
                }
            }
        }
    }
}

@Composable
private fun InTidal() {
    val c = Ab.colors.keep
    Box(Modifier.clip(Ab.shapes.small).background(c.copy(alpha = 0.14f)).padding(horizontal = 6.dp, vertical = 2.dp)) {
        AbText("IN TIDAL", style = Ab.type.overline, color = c, maxLines = 1)
    }
}

@Composable
private fun Stat(value: String, label: String, modifier: Modifier = Modifier) {
    Column(modifier.clip(Ab.shapes.medium).background(Ab.colors.surface).padding(Ab.space.m)) {
        AbText(value, style = Ab.type.title, maxLines = 1)
        AbText(label, style = Ab.type.caption, color = Ab.colors.textMuted, maxLines = 1)
    }
}

/** The whole map small, every track faint and this context's lit; drawn once, from the map already on the device. */
@Composable
private fun ContextOnMap(graph: DataGraph, members: Set<Long>, hue: Float) {
    val data by graph.map.data.collectAsStateWithLifecycle()
    LaunchedEffect(Unit) { graph.map.load() }
    val d = data
    val points = remember(d?.version, members) {
        d?.let { m ->
            val rest = ArrayList<Float>(m.points.size * 2)
            val lit = ArrayList<Float>(members.size * 2)
            m.points.forEach { p -> val to = if (p[0].toLong() in members) lit else rest; to.add(p[1] / m.scale); to.add(p[2] / m.scale) }
            rest to lit
        }
    }
    val faint = Ab.colors.textFaint.copy(alpha = 0.25f)
    val colour = contextColor(hue)
    Canvas(Modifier.fillMaxWidth().height(140.dp).clip(Ab.shapes.medium).background(Ab.colors.surface).semantics { contentDescription = "This context on the map" }) {
        val (rest, lit) = points ?: return@Canvas
        val pad = 8.dp.toPx()
        // the map is square: fit it to the width, and show the band of it where this context is
        val side = size.width - pad * 2
        val litY = lit.filterIndexed { i, _ -> i % 2 == 1 }.average().toFloat().takeIf { !it.isNaN() } ?: 0.5f
        val top = (size.height / 2 - litY * side).coerceIn(size.height - side - pad, pad)
        fun offsets(xs: List<Float>) = xs.chunked(2) { Offset(pad + it[0] * side, top + it[1] * side) }
        drawPoints(offsets(rest), PointMode.Points, faint, 2.dp.toPx(), StrokeCap.Round)
        drawPoints(offsets(lit), PointMode.Points, colour, 3.5.dp.toPx(), StrokeCap.Round)
    }
}

// A track

@Composable
fun TrackScreen(vm: AppViewModel, graph: DataGraph, id: Long, padding: PaddingValues, nav: CatalogueNav) {
    val verdicts by vm.verdicts.collectAsStateWithLifecycle()
    val lists by vm.lists.collectAsStateWithLifecycle()
    val context = LocalContext.current
    Loaded<TrackStory>(id, { graph.api.trackStory(id) }) { story ->
        val t = story.track
        val (sigil, faint) = t.sigil()
        LazyColumn(Modifier.fillMaxSize(), contentPadding = padding) {
            item(key = "h") {
                Column(verticalArrangement = Arrangement.spacedBy(Ab.space.s)) {
                    Box(Modifier.fillMaxWidth().padding(vertical = Ab.space.m), contentAlignment = Alignment.Center) { SoundSigil(sigil, size = 140.dp, faint = faint) }
                    Title(t.title, t.ageTag())
                    AbText(t.artist, Modifier.clickable { nav.artist(story.artistId) }.heightIn(min = 32.dp), style = Ab.type.body, color = Ab.colors.textMuted)
                    Facts(listOfNotNull(
                        t.sound?.bpm?.let { "${it.roundToInt()} BPM" }, story.key.ifBlank { null }?.let(::keyName),
                        story.danceability?.let { "danceability ${"%.2f".format(it)}" }, released(story.released)?.let { "released $it" },
                    ).joinToString(" · "))
                    VerdictBar((verdicts[t.ref.key] ?: Verdict.fromApi(t.verdict))?.style(), { v -> vm.give(v.verdict(), t, "track") }, Modifier.padding(top = Ab.space.s))
                }
            }
            item(key = "story") { Story(story) }
            item(key = "where") {
                Column(Modifier.padding(top = Ab.space.xl), verticalArrangement = Arrangement.spacedBy(Ab.space.s)) {
                    SectionHead("", Ab.colors.textMuted, "Where it lives", "")
                    story.context?.let { c ->
                        DoorCard({ nav.context(c.id) }) {
                            Row(verticalAlignment = Alignment.CenterVertically) {
                                HueDot(c.hue, c.near)
                                Spacer(Modifier.width(Ab.space.m))
                                Column {
                                    AbText(shortName(c.name), style = Ab.type.label, maxLines = 1)
                                    AbText(if (c.near) "Sounds closest to it; not in it yet" else "Its context · ${count(c.recent)} plays lately", style = Ab.type.caption, color = Ab.colors.textMuted)
                                }
                            }
                        }
                    }
                    story.albumId?.let { album -> if (t.album.isNotBlank()) DoorCard({ nav.album(album) }) { Column { AbText(t.album, style = Ab.type.label, maxLines = 1); AbText("Its album", style = Ab.type.caption, color = Ab.colors.textMuted) } } }
                    if (story.lists.isNotEmpty()) {
                        Caption("In ${story.lists.size} of your lists", Modifier.padding(top = Ab.space.s))
                        val ages = byAge(lists.lists).flatMap { (age, ls) -> ls.map { it.key to (age to it) } }.toMap()
                        FlowRow(horizontalArrangement = Arrangement.spacedBy(Ab.space.s), verticalArrangement = Arrangement.spacedBy(Ab.space.s)) {
                            story.lists.forEach { l ->
                                Row(
                                    Modifier.clip(Ab.shapes.pill).background(Ab.colors.raised).clickable { nav.list(l.key) }.heightIn(min = 40.dp).padding(horizontal = Ab.space.m),
                                    verticalAlignment = Alignment.CenterVertically,
                                ) {
                                    AbText(shortName(l.title), style = Ab.type.caption, maxLines = 1)
                                    ages[l.key]?.let { (age, list) -> age.tag(list) }?.let { Spacer(Modifier.width(Ab.space.s)); AgeTagPill(it) }
                                }
                            }
                        }
                    }
                    Row(Modifier.padding(top = Ab.space.m), horizontalArrangement = Arrangement.spacedBy(Ab.space.s)) {
                        story.tidalUrl?.let { url -> AbButton("Play in Tidal", { openUrl(context, url) }, Modifier.weight(1f)) }
                        AbButton("Radio from this", { vm.startRadio(t) }, Modifier.weight(1f), kind = ButtonKind.Quiet)
                    }
                }
            }
        }
    }
}

/** "Eb maj" -> "E♭ maj", "F# minor" -> "F♯ minor". */
fun keyName(key: String) = key.replace(Regex("^([A-G])b"), "$1♭").replace("#", "♯")

private val RELEASED = DateTimeFormatter.ofPattern("d MMM yyyy", Locale.UK)

/** MusicBrainz's partial dates: "2026-05-22" -> "22 May 2026", "2019-03" -> "Mar 2019", "2019" stays. */
fun released(date: String): String? = when (date.length) {
    10 -> runCatching { LocalDate.parse(date).format(RELEASED) }.getOrNull()
    7 -> monthLabel(date)
    4 -> date
    else -> null
}

/** When you found it and how its plays have gone: by month for a track found this past year, by year before. */
@Composable
private fun Story(story: TrackStory) {
    val t = story.track
    val colour = if (t.hue != null && !t.hueNear) contextColor(t.hue) else Ab.colors.textMuted
    Column(Modifier.padding(top = Ab.space.xl), verticalArrangement = Arrangement.spacedBy(Ab.space.s)) {
        SectionHead("", Ab.colors.textMuted, "Its story", "")
        val first = story.firstPlayed?.let(::localDate)
        val plays = t.plays ?: 0
        if (first == null) { Caption("Not played yet"); return@Column }
        Caption(listOfNotNull("First played ${if (first.year == LocalDate.now().year) "in ${first.month.getDisplayName(java.time.format.TextStyle.FULL, Locale.UK)}" else dayLabel(first)}", "${count(plays)} plays", t.recent?.takeIf { it > 0 }?.let { "$it in the last 45 days" }).joinToString(" · "))
        if (story.months.isNotEmpty()) {
            val start = YearMonth.from(first).minusMonths(2)
            val months = generateSequence(start) { it.plusMonths(1) }.takeWhile { !it.isAfter(YearMonth.now()) }.toList()
            val values = months.map { story.months[it.toString()] ?: 0 }
            PlayBars(values, colour, "Plays by month: " + months.zip(values).filter { it.second > 0 }.joinToString { "${it.second} in ${it.first.month.getDisplayName(java.time.format.TextStyle.SHORT, Locale.UK)}" }, height = 70.dp)
            Row(Modifier.fillMaxWidth()) { months.forEach { AbText(it.month.getDisplayName(java.time.format.TextStyle.SHORT, Locale.UK).uppercase(), Modifier.weight(1f), style = Ab.type.number, color = Ab.colors.textMuted, maxLines = 1) } }
        } else {
            val bars = yearBars(story.years)
            YearBars(bars, "Plays per year: ${bars.filter { it.plays > 0 }.joinToString { "${it.plays} in ${it.year}" }}", height = 64.dp, gap = 4.dp)
            YearAxis(FIRST_YEAR, Year.now().value)
            t.peak?.let { Caption("Played most in ${monthLabel(it)}") }
        }
    }
}

// Search

/**
 * One search over everything: the best match first, with its numbers, then a few of each kind with a way to all of
 * them. Chips at the top say how many of each there are and jump there.
 */
@Composable
fun SearchScreen(vm: AppViewModel, graph: DataGraph, initial: String, padding: PaddingValues, nav: CatalogueNav) {
    val verdicts by vm.verdicts.collectAsStateWithLifecycle()
    var query by remember { mutableStateOf(initial) }
    val q = debounced(query)
    var result by remember { mutableStateOf<LibrarySearch?>(null) }
    var loading by remember { mutableStateOf(false) }
    var error by remember { mutableStateOf<String?>(null) }
    val focus = remember { FocusRequester() }
    val list = rememberLazyListState()
    val scope = rememberCoroutineScope()
    LaunchedEffect(Unit) { if (initial.isBlank()) runCatching { focus.requestFocus() } }
    LaunchedEffect(q) {
        if (q.isBlank()) { result = null; return@LaunchedEffect }
        loading = true
        try { result = graph.api.librarySearch(q); error = null } catch (e: ApiException) { error = e.message }
        loading = false
    }
    val r = result
    // section keys in the order they're shown, for the jump chips
    val sections = buildList {
        if (r != null) {
            if (r.tracks.total > 0) add(Triple("tracks", "Tracks", r.tracks.total))
            if (r.artists.items.isNotEmpty()) add(Triple("artists", "Artists", r.artists.total))
            if (r.albums.total > 0) add(Triple("albums", "Albums", r.albums.total))
            if (r.lists.total > 0) add(Triple("lists", "Lists", r.lists.total))
            if (r.sets.total > 0) add(Triple("sets", "Sets", r.sets.total))
            if (r.discover.total > 0) add(Triple("discover", "Discover", r.discover.total))
        }
    }
    LazyColumn(Modifier.fillMaxSize(), state = list, contentPadding = padding) {
        item(key = "head") {
            Column(verticalArrangement = Arrangement.spacedBy(Ab.space.m)) {
                AbTextField(query, { query = it }, "Search everything", Modifier.padding(top = Ab.space.s), placeholder = "A track, artist, album or set", focus = focus)
                if (sections.isNotEmpty()) LazyRow(horizontalArrangement = Arrangement.spacedBy(Ab.space.s)) {
                    items(sections, key = { it.first }) { (key, label, n) ->
                        AbChip("$label ${count(n)}") {
                            val index = list.layoutInfo.totalItemsCount.let { _ -> sectionIndex(key, r!!) }
                            scope.launch { list.animateScrollToItem(index) }
                        }
                    }
                }
                if (loading && r == null) Spinner(size = 14.dp)
                error?.let { AbText(it, style = Ab.type.caption, color = Ab.colors.no) }
                if (r != null && sections.isEmpty() && r.best == null) EmptyState("Nothing found", "Try fewer words.")
            }
        }
        if (r == null) return@LazyColumn
        item(key = "best") { r.best?.let { b -> BestCard(b) { nav.artist(b.id) } } }
        fun more(total: Int, shown: Int, open: () -> Unit) = if (total > shown) listOf("All ${count(total)}" to open) else emptyList()
        item(key = "tracks") {
            if (r.tracks.items.isNotEmpty()) DiscoverSection("${count(r.tracks.total)} tracks", Ab.colors.textMuted, "Tracks", "Played lately first", more(r.tracks.total, r.tracks.items.size) { nav.tracks("", q) }) {
                r.tracks.items.forEach { t -> CardRow { LibraryTrackRow(t, verdicts[t.ref.key], { v -> vm.give(v, t, "search") }, { t.openWith(nav, "search") }, { nav.menu(t, "search") }) } }
            }
        }
        item(key = "artists") {
            if (r.artists.items.isNotEmpty()) DiscoverSection("${count(r.artists.total)} artists", Ab.colors.textMuted, "Artists", "Most played first", more(r.artists.total, r.artists.items.size + (if (r.best != null) 1 else 0)) { nav.artists(q) }) {
                r.artists.items.forEach { a -> CardRow { ArtistLine(a) { nav.artist(a.id) } } }
            }
        }
        item(key = "albums") {
            if (r.albums.items.isNotEmpty()) DiscoverSection("${count(r.albums.total)} albums", Ab.colors.textMuted, "Albums", "Most played first", more(r.albums.total, r.albums.items.size) { nav.albums(q) }) {
                r.albums.items.forEach { a -> CardRow { AlbumLine(a) { nav.album(a.id) } } }
            }
        }
        item(key = "lists") {
            if (r.lists.items.isNotEmpty()) DiscoverSection("${count(r.lists.total)} lists", Ab.colors.textMuted, "Lists", "", emptyList()) {
                r.lists.items.forEach { l ->
                    CardRow {
                        Column(Modifier.fillMaxWidth().clickable { nav.list(l.key) }.heightIn(min = 52.dp).padding(vertical = Ab.space.s)) {
                            AbText(shortName(l.title), style = Ab.type.label, maxLines = 1)
                            if (l.subtitle.isNotBlank()) AbText(l.subtitle, style = Ab.type.caption, color = Ab.colors.textMuted, maxLines = 1)
                        }
                    }
                }
            }
        }
        item(key = "sets") {
            if (r.sets.items.isNotEmpty()) DiscoverSection("${count(r.sets.total)} sets", Ab.colors.ageHabit, "Sets", "By how many of its tracks match", more(r.sets.total, r.sets.items.size) { nav.sets(q) }) {
                r.sets.items.forEach { s -> CardRow { SetLine(s) { nav.set(s.id) } } }
            }
        }
        item(key = "discover") {
            if (r.discover.items.isNotEmpty()) DiscoverSection("New · ${r.discover.total} waiting", Ab.colors.ageNew, "In Discover", "New releases waiting in your queue", listOf("Open the queue" to { nav.list("releases") })) {
                r.discover.items.forEach { rel ->
                    CardRow {
                        Column(Modifier.fillMaxWidth().padding(vertical = Ab.space.s)) {
                            AbText(rel.title, style = Ab.type.label, maxLines = 1)
                            AbText(rel.artist, style = Ab.type.caption, color = Ab.colors.textMuted, maxLines = 1)
                        }
                    }
                }
            }
        }
    }
}

/** The lazy index of a search section: head, best, then the sections in order. */
private fun sectionIndex(key: String, r: LibrarySearch) = listOf("tracks", "artists", "albums", "lists", "sets", "discover").indexOf(key) + 2

/** The best match: an artist you play, with plays in all and lately, and this year's plays by month. */
@Composable
private fun BestCard(b: com.georgewaller.audiobrain.data.BestMatch, onOpen: () -> Unit) {
    Column(
        Modifier.fillMaxWidth().padding(top = Ab.space.l).clip(Ab.shapes.large).background(Ab.colors.surface).border(1.dp, Ab.colors.line, Ab.shapes.large)
            .clickable(onClick = onOpen).padding(Ab.space.l),
        verticalArrangement = Arrangement.spacedBy(Ab.space.s),
    ) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Overline("Artist", color = Ab.colors.textMuted)
            artistTag(b.years)?.let { Spacer(Modifier.width(Ab.space.s)); AgeTagPill(it) }
        }
        AbText(b.name, style = Ab.type.display.copy(fontSize = 28.sp, lineHeight = 30.sp), maxLines = 2)
        Facts("${count(b.tracks)} tracks · ${count(b.listens)} plays · ${count(b.recent)} in the last 45 days")
        val thisYear = b.months.sum()
        if (thisYear > 0) {
            PlayBars(b.months, Ab.colors.textMuted, "Plays this year by month: " + monthLabels(b.months.size).zip(b.months).joinToString { "${it.second} in ${it.first}" }, height = 32.dp)
            Caption("${count(thisYear)} plays this year" + (sinceMonth(b.months)?.let { ", most of them since $it" } ?: ""))
        }
    }
}
