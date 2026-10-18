package com.georgewaller.audiobrain.pages

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.derivedStateOf
import androidx.compose.runtime.getValue
import androidx.compose.runtime.remember
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.georgewaller.audiobrain.AppViewModel
import com.georgewaller.audiobrain.ItemRow
import com.georgewaller.audiobrain.data.ListItem
import com.georgewaller.audiobrain.design.Ab
import com.georgewaller.audiobrain.design.AbChip
import com.georgewaller.audiobrain.design.AbText
import com.georgewaller.audiobrain.design.AbTextField
import com.georgewaller.audiobrain.design.EmptyState
import com.georgewaller.audiobrain.design.SegmentTabs
import com.georgewaller.audiobrain.design.Spinner
import com.georgewaller.audiobrain.design.TrackRow
import com.georgewaller.audiobrain.pages.LibraryViewModel.Tab
import kotlin.math.roundToInt

/**
 * Catalogue: your library, like any music library. Tracks, artists, albums and sets, each searchable and sortable;
 * an artist opens to their albums and tracks, an album to its tracks, a set to its tracklist.
 */
@Composable
fun CataloguePage(
    vm: AppViewModel,
    library: LibraryViewModel,
    padding: PaddingValues,
    onArtist: (Long) -> Unit,
    onAlbum: (Long) -> Unit,
    onSet: (Long) -> Unit,
    onMenu: (ListItem, String) -> Unit,
) {
    val s by library.state.collectAsStateWithLifecycle()
    val verdicts by vm.verdicts.collectAsStateWithLifecycle()
    val list = rememberLazyListState()
    val nearEnd by remember { derivedStateOf { list.layoutInfo.visibleItemsInfo.lastOrNull()?.index?.let { it >= list.layoutInfo.totalItemsCount - 6 } ?: false } }
    LaunchedEffect(nearEnd, s.tab) { if (nearEnd) library.more() }

    LazyColumn(Modifier.fillMaxSize(), state = list, contentPadding = padding) {
        item(key = "head") {
            Column(verticalArrangement = Arrangement.spacedBy(Ab.space.s)) {
                Row(Modifier.padding(top = Ab.space.s), verticalAlignment = Alignment.CenterVertically) {
                    AbText("Catalogue", style = Ab.type.display, modifier = Modifier.weight(1f))
                    if (s.loading) Spinner(size = 14.dp)
                }
                AbTextField(s.query, library::setQuery, "Search", placeholder = when (s.tab) {
                    Tab.Tracks -> "Title, artist or album"
                    Tab.Artists -> "Artist"
                    Tab.Albums -> "Album or artist"
                    Tab.Sets -> "Set, DJ, or a track in the tracklist"
                })
                SegmentTabs(Tab.entries.map { it.label }, s.tab.ordinal, { library.setTab(Tab.entries[it]) })
                Row(Modifier.fillMaxWidth().padding(vertical = Ab.space.xs), horizontalArrangement = Arrangement.spacedBy(Ab.space.s), verticalAlignment = Alignment.CenterVertically) {
                    if (s.tab == Tab.Sets) {
                        listOf("" to "All", "played" to "Played", "saved" to "Saved", "new" to "New").forEach { (k, label) -> AbChip(label, selected = s.setSource == k) { library.setSource(k) } }
                    } else {
                        val sorts = if (s.tab == Tab.Tracks) listOf("plays" to "Most played", "recent" to "Recent", "az" to "A–Z") else listOf("plays" to "Most played", "az" to "A–Z")
                        sorts.forEach { (k, label) -> AbChip(label, selected = s.sort == k) { library.setSort(k) } }
                    }
                }
                if (s.tab == Tab.Tracks && s.total != null) AbText("${s.total} tracks", style = Ab.type.number, color = Ab.colors.textFaint)
                s.error?.let { AbText(it, style = Ab.type.caption, color = Ab.colors.no) }
            }
        }
        when (s.tab) {
            Tab.Tracks -> items(s.tracks, key = { "t-${it.trackId}" }) { t -> ItemRow(t, verdicts[t.ref.key], { v -> vm.give(v, t, "catalogue") }, { onMenu(t, "catalogue") }) }
            Tab.Artists -> items(s.artists, key = { "a-${it.id}" }) { a ->
                TrackRow(a.name, "${a.tracks} tracks", facts = "${a.plays.roundToInt()}\nplays", onClick = { onArtist(a.id) })
            }
            Tab.Albums -> items(s.albums, key = { "al-${it.id}" }) { a ->
                TrackRow(a.name, "${a.artist} · ${a.tracks} ${if (a.tracks == 1) "track" else "tracks"}", facts = "${a.plays.roundToInt()}\nplays", onClick = { onAlbum(a.id) })
            }
            Tab.Sets -> items(s.sets, key = { "s-${it.id}" }) { set ->
                val detail = listOfNotNull(set.by, set.minutes?.let { "$it min" }, set.source, if (set.entries > 0) "${set.inCatalogue}/${set.entries} in your catalogue" else "no tracklist").joinToString(" · ")
                TrackRow(set.title, detail, onClick = { onSet(set.id) })
            }
        }
        val empty = when (s.tab) { Tab.Tracks -> s.tracks.isEmpty(); Tab.Artists -> s.artists.isEmpty(); Tab.Albums -> s.albums.isEmpty(); Tab.Sets -> s.sets.isEmpty() }
        if (empty && !s.loading && s.error == null) item(key = "empty") { EmptyState("Nothing found", if (s.query.isBlank()) "Your library is empty so far." else "Try fewer words.") }
    }
}
