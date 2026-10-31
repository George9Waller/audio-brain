package com.georgewaller.audiobrain.overlays

import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.LazyListScope
import androidx.compose.foundation.lazy.LazyListState
import androidx.compose.foundation.lazy.itemsIndexed
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.runtime.Composable
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.derivedStateOf
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.georgewaller.audiobrain.AlbumLine
import com.georgewaller.audiobrain.AppViewModel
import com.georgewaller.audiobrain.ArtistLine
import com.georgewaller.audiobrain.CatalogueNav
import com.georgewaller.audiobrain.FIRST_YEAR
import com.georgewaller.audiobrain.LibraryTrackRow
import com.georgewaller.audiobrain.Paged
import com.georgewaller.audiobrain.Recency
import com.georgewaller.audiobrain.RecencyBar
import com.georgewaller.audiobrain.SectionHead
import com.georgewaller.audiobrain.SetLine
import com.georgewaller.audiobrain.Tile
import com.georgewaller.audiobrain.cardSlice
import com.georgewaller.audiobrain.color
import com.georgewaller.audiobrain.count
import com.georgewaller.audiobrain.data.DataGraph
import com.georgewaller.audiobrain.data.LanguageCount
import com.georgewaller.audiobrain.data.ListItem
import com.georgewaller.audiobrain.design.Ab
import com.georgewaller.audiobrain.design.AbButton
import com.georgewaller.audiobrain.design.AbChip
import com.georgewaller.audiobrain.design.AbDivider
import com.georgewaller.audiobrain.design.AbText
import com.georgewaller.audiobrain.design.AbTextField
import com.georgewaller.audiobrain.design.ButtonKind
import com.georgewaller.audiobrain.design.EmptyState
import com.georgewaller.audiobrain.design.LocalRowGround
import com.georgewaller.audiobrain.design.Spinner
import com.georgewaller.audiobrain.pages.LibraryViewModel
import kotlinx.coroutines.delay
import java.time.Year

/** Rows in one card, made of lazy items so a long list still loads as it scrolls. */
fun <T> LazyListScope.cardItems(list: List<T>, key: (T) -> Any, row: @Composable (T) -> Unit) {
    itemsIndexed(list, key = { _, t -> key(t) }) { i, t ->
        Column(Modifier.cardSlice(i == 0, i == list.lastIndex)) {
            CompositionLocalProvider(LocalRowGround provides Ab.colors.surface) { row(t) }
            if (i < list.lastIndex) AbDivider()
        }
    }
}

/** Load the next page when the list nears its end. */
@Composable
fun LoadMore(state: LazyListState, paged: Paged<*>) {
    val nearEnd by remember(state) { derivedStateOf { state.layoutInfo.visibleItemsInfo.lastOrNull()?.index?.let { it >= state.layoutInfo.totalItemsCount - 6 } ?: false } }
    LaunchedEffect(nearEnd, paged) { if (nearEnd) paged.more() }
}

/** A query typed into a list, applied after a pause. */
@Composable
fun debounced(value: String, wait: Long = 350): String {
    var settled by remember { mutableStateOf(value) }
    LaunchedEffect(value) { if (value != settled) { delay(wait); settled = value } }
    return settled
}

@Composable
private fun Heading(title: String, total: String?, loading: Boolean, trailing: (@Composable () -> Unit)? = null) {
    Row(Modifier.fillMaxWidth().padding(top = Ab.space.s), verticalAlignment = Alignment.CenterVertically) {
        AbText(title, Modifier.weight(1f), style = Ab.type.display)
        if (loading) Spinner(size = 14.dp)
        total?.let { AbText(it, Modifier.padding(start = Ab.space.s), style = Ab.type.number, color = Ab.colors.textMuted) }
        trailing?.invoke()
    }
}

/** What's still loading, failed, or empty, at the foot of a paged list. */
private fun LazyListScope.footer(paged: Paged<*>, empty: String) {
    item(key = "foot") {
        when {
            paged.error != null -> EmptyState("Couldn't load it", paged.error.orEmpty()) { AbButton("Try again", paged::retry, kind = ButtonKind.Quiet) }
            paged.items.isEmpty() && !paged.loading -> EmptyState("Nothing here", empty)
            else -> Unit
        }
    }
}

/**
 * Tracks by when you last played them (A): the library split in four, each part a filter with its count, and each
 * sorted the way it needs: most played lately, last played, most played back then, closest to your taste.
 * Tapping the chosen part again shows every track, most played first.
 */
@Composable
fun TracksScreen(vm: AppViewModel, graph: DataGraph, library: LibraryViewModel, start: String, initialQuery: String, startLanguage: String, padding: PaddingValues, nav: CatalogueNav) {
    val summary by library.state.collectAsStateWithLifecycle()
    val verdicts by vm.verdicts.collectAsStateWithLifecycle()
    var recency by remember { mutableStateOf(Recency.entries.firstOrNull { it.key == start }) }
    var query by remember { mutableStateOf(initialQuery) }
    var language by remember { mutableStateOf(startLanguage) }
    val q = debounced(query)
    val scope = rememberCoroutineScope()
    var total by remember(recency, q, language) { mutableStateOf<Int?>(null) }
    // a language's own split by when comes with its first page; keep it while other parts of it are shown
    var languageCounts by remember(language) { mutableStateOf<Map<String, Int>?>(null) }
    val paged = remember(recency, q, language) {
        Paged(scope) { offset ->
            graph.api.libraryTracks(q, "plays", offset, recency?.key.orEmpty(), language).also { page -> total = page.total; page.recency?.let { languageCounts = it } }.items
        }
    }
    val languages = summary.summary?.languages.orEmpty()
    val counts = if (language.isBlank()) summary.summary?.recency.orEmpty() else languageCounts.orEmpty()
    val name = languages.firstOrNull { it.code == language }?.name
    val list = rememberLazyListState()
    LoadMore(list, paged)
    LazyColumn(Modifier.fillMaxSize(), state = list, contentPadding = padding) {
        item(key = "head") {
            Column(verticalArrangement = Arrangement.spacedBy(Ab.space.m)) {
                Heading(name ?: "Library", if (name != null) languageCounts?.values?.sum()?.let { "${count(it)} tracks" } else summary.summary?.tracks?.let { "${count(it)} tracks" }, paged.loading && paged.items.isEmpty())
                AbTextField(query, { query = it }, "", placeholder = "A track, artist or album")
                LanguageChips(languages, language) { language = it }
                if (counts.isNotEmpty()) {
                    RecencyBar(counts, Modifier.padding(top = Ab.space.xs), height = 14.dp, selected = recency) { recency = if (recency == it) null else it }
                    Recency.entries.chunked(2).forEach { pair ->
                        Row(horizontalArrangement = Arrangement.spacedBy(Ab.space.s)) {
                            pair.forEach { r ->
                                Tile(r.label, count(counts[r.key] ?: 0), Modifier.weight(1f), dot = r.color(), selected = recency == r) { recency = if (recency == r) null else r }
                            }
                        }
                    }
                }
                Row(Modifier.padding(top = Ab.space.s, bottom = Ab.space.s), verticalAlignment = Alignment.CenterVertically) {
                    val r = recency
                    AbText(
                        if (q.isNotBlank()) "${total?.let(::count) ?: "…"} matching “$q”" else r?.caption ?: "All your ${name?.let { "$it " } ?: ""}tracks, most played first",
                        Modifier.weight(1f), style = Ab.type.caption, color = Ab.colors.textMuted, maxLines = 2,
                    )
                    AbText("${r?.sort ?: "Most played"} ↓", style = Ab.type.number, color = Ab.colors.textMuted)
                }
            }
        }
        cardItems(paged.items, { "t-${it.trackId}" }) { t ->
            LibraryTrackRow(t, verdicts[t.ref.key], { v -> vm.give(v, t, "catalogue") }, { t.trackId?.let(nav.track) }, { nav.menu(t, "catalogue") })
        }
        footer(paged, if (q.isBlank()) "No tracks here yet." else "Try fewer words.")
    }
}

/** Artists, then and now (B): a bar per year from 2016, their biggest year and this one lit, sorted by this year or all time. */
@Composable
fun ArtistsScreen(graph: DataGraph, library: LibraryViewModel, initialQuery: String, padding: PaddingValues, nav: CatalogueNav) {
    val summary by library.state.collectAsStateWithLifecycle()
    var sort by remember { mutableStateOf("year") }
    var query by remember { mutableStateOf(initialQuery) }
    var language by remember { mutableStateOf("") }
    val q = debounced(query)
    val scope = rememberCoroutineScope()
    val paged = remember(sort, q, language) { Paged(scope) { offset -> graph.api.libraryArtists(q, sort, offset, language) } }
    val list = rememberLazyListState()
    LoadMore(list, paged)
    LazyColumn(Modifier.fillMaxSize(), state = list, contentPadding = padding) {
        item(key = "head") {
            Column(verticalArrangement = Arrangement.spacedBy(Ab.space.m)) {
                Heading("Artists", summary.summary?.artists?.let(::count), paged.loading && paged.items.isEmpty())
                AbTextField(query, { query = it }, "", placeholder = "An artist")
                LanguageChips(summary.summary?.languages.orEmpty(), language) { language = it }
                Row(horizontalArrangement = Arrangement.spacedBy(Ab.space.s)) {
                    AbChip("This year", selected = sort == "year") { sort = "year" }
                    AbChip("All time", selected = sort == "plays") { sort = "plays" }
                }
                Row(Modifier.padding(bottom = Ab.space.s)) {
                    AbText(if (sort == "year") "Most played this year" else "Most played in all", Modifier.weight(1f), style = Ab.type.caption, color = Ab.colors.textMuted)
                    AbText("Plays per year · $FIRST_YEAR → ${Year.now().value}", style = Ab.type.number, color = Ab.colors.textMuted)
                }
            }
        }
        cardItems(paged.items, { "a-${it.id}" }) { a -> ArtistLine(a) { nav.artist(a.id) } }
        footer(paged, "No artists found.")
    }
}

/**
 * Albums: played lately, then your most played; or all time, or the ones you've played some of but not all. Each
 * icon fills in as more of it is played, with a bar for how much.
 */
@Composable
fun AlbumsScreen(graph: DataGraph, library: LibraryViewModel, initialQuery: String, padding: PaddingValues, nav: CatalogueNav) {
    val summary by library.state.collectAsStateWithLifecycle()
    var sort by remember { mutableStateOf("lately") }
    var query by remember { mutableStateOf(initialQuery) }
    val q = debounced(query)
    val scope = rememberCoroutineScope()
    val lately = remember(q) { Paged(scope) { offset -> if (offset > 0) emptyList() else graph.api.libraryAlbums(q, "lately", 0) } }
    val paged = remember(sort, q) { Paged(scope) { offset -> graph.api.libraryAlbums(q, if (sort == "lately") "all" else sort, offset) } }
    val list = rememberLazyListState()
    LoadMore(list, paged)
    LazyColumn(Modifier.fillMaxSize(), state = list, contentPadding = padding) {
        item(key = "head") {
            Column(verticalArrangement = Arrangement.spacedBy(Ab.space.m)) {
                Heading("Albums", summary.summary?.albums?.let(::count), paged.loading && paged.items.isEmpty())
                AbTextField(query, { query = it }, "", placeholder = "An album or artist")
                Row(horizontalArrangement = Arrangement.spacedBy(Ab.space.s)) {
                    listOf("lately" to "Lately", "all" to "All time", "unfinished" to "Not finished").forEach { (k, label) -> AbChip(label, selected = sort == k) { sort = k } }
                }
            }
        }
        if (sort == "lately" && lately.items.isNotEmpty()) {
            item(key = "lately-head") { SectionHead("Now · ${lately.items.size}${if (lately.items.size >= 50) "+" else ""} albums", Ab.colors.ageNow, "Played lately", "Most played in the last 45 days first", Modifier.padding(top = Ab.space.xl)) }
            cardItems(lately.items, { "l-${it.id}" }) { a -> AlbumLine(a) { nav.album(a.id) } }
        }
        item(key = "all-head") {
            val (title, caption) = when (sort) {
                "unfinished" -> "Not finished" to "Albums you've played some of, but not all; most played first"
                else -> "Your most played" to "By plays in all"
            }
            SectionHead("", Ab.colors.textMuted, title, caption, Modifier.padding(top = Ab.space.xl))
        }
        cardItems(paged.items, { "a-${it.id}" }) { a -> AlbumLine(a) { nav.album(a.id) } }
        footer(paged, "No albums found.")
    }
}

/** Sets: played, saved and new, each with its length, when, and a strip for how much of its tracklist you play. */
@Composable
fun SetsScreen(graph: DataGraph, library: LibraryViewModel, initialQuery: String, padding: PaddingValues, nav: CatalogueNav) {
    val summary by library.state.collectAsStateWithLifecycle()
    var tab by remember { mutableStateOf("played") }
    var query by remember { mutableStateOf(initialQuery) }
    val q = debounced(query)
    val scope = rememberCoroutineScope()
    val paged = remember(tab, q) { Paged(scope) { offset -> graph.api.librarySets(q, tab, offset) } }
    val counts = summary.summary?.sets.orEmpty()
    val list = rememberLazyListState()
    LoadMore(list, paged)
    LazyColumn(Modifier.fillMaxSize(), state = list, contentPadding = padding) {
        item(key = "head") {
            Column(verticalArrangement = Arrangement.spacedBy(Ab.space.m)) {
                Heading("Sets", null, paged.loading && paged.items.isEmpty()) {
                    AbButton("Paste a tracklist", nav.pasteTracklist, kind = ButtonKind.Text)
                }
                AbTextField(query, { query = it }, "", placeholder = "A set, DJ, or a track in the tracklist")
                Row(horizontalArrangement = Arrangement.spacedBy(Ab.space.s)) {
                    listOf("played" to "Played", "saved" to "Saved", "new" to "New").forEach { (k, label) ->
                        AbChip(counts[k]?.let { "$label ${count(it)}" } ?: label, selected = tab == k) { tab = k }
                    }
                }
                AbText(
                    when (tab) {
                        "played" -> "Sets you've listened to, last played first"
                        "saved" -> "Your YouTube sets playlist and tracklists you gave, newest first"
                        else -> "New uploads on channels you save from, most of the tracklist you play first"
                    },
                    Modifier.padding(bottom = Ab.space.s), style = Ab.type.caption, color = Ab.colors.textMuted,
                )
            }
        }
        cardItems(paged.items, { "s-${it.id}" }) { s -> SetLine(s) { nav.set(s.id) } }
        footer(paged, "No sets here yet.")
    }
}

/** Any language, or one of those you play besides English; tapping the chosen one again goes back to any. */
@Composable
private fun LanguageChips(languages: List<LanguageCount>, selected: String, onPick: (String) -> Unit) {
    if (languages.isEmpty()) return
    androidx.compose.foundation.lazy.LazyRow(horizontalArrangement = Arrangement.spacedBy(Ab.space.s)) {
        item { AbChip("Any language", selected = selected.isBlank()) { onPick("") } }
        items(languages.size) { i -> val l = languages[i]; AbChip(l.name, selected = selected == l.code) { onPick(if (selected == l.code) "" else l.code) } }
    }
}

/** Open a list's row as a track's page when it's a track; otherwise its menu. */
fun ListItem.openWith(nav: CatalogueNav, where: String) { trackId?.let(nav.track) ?: nav.menu(this, where) }

@Composable
internal fun Link(label: String, onClick: () -> Unit) {
    Row(Modifier.clickable(onClick = onClick).heightIn(min = 44.dp), verticalAlignment = Alignment.CenterVertically) {
        AbText(label, style = Ab.type.label)
    }
}
