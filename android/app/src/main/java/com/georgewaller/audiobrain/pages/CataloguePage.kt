package com.georgewaller.audiobrain.pages

import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.LazyRow
import androidx.compose.foundation.lazy.items
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.georgewaller.audiobrain.CatalogueNav
import com.georgewaller.audiobrain.RecencyBar
import com.georgewaller.audiobrain.RecencyKey
import com.georgewaller.audiobrain.SectionHead
import com.georgewaller.audiobrain.Tile
import com.georgewaller.audiobrain.count
import com.georgewaller.audiobrain.data.Shelf
import com.georgewaller.audiobrain.design.Ab
import com.georgewaller.audiobrain.design.AbText
import com.georgewaller.audiobrain.design.AskBox
import com.georgewaller.audiobrain.design.EmptyState
import com.georgewaller.audiobrain.design.ListSwatch
import com.georgewaller.audiobrain.design.Overline
import com.georgewaller.audiobrain.design.Spinner
import com.georgewaller.audiobrain.track

/**
 * Catalogue: the library home. One search for everything; the library split by when you last played each track;
 * what you've been in lately (albums, sets, a context); and a door to each part with its size.
 */
@Composable
fun CataloguePage(library: LibraryViewModel, padding: PaddingValues, nav: CatalogueNav) {
    val s by library.state.collectAsStateWithLifecycle()
    val summary = s.summary
    LazyColumn(Modifier.fillMaxSize(), contentPadding = padding) {
        item(key = "head") {
            Column(Modifier.padding(top = Ab.space.s), verticalArrangement = Arrangement.spacedBy(Ab.space.m)) {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    AbText("Catalogue", Modifier.weight(1f), style = Ab.type.display)
                    if (s.loading) Spinner(size = 14.dp)
                }
                AskBox("Search tracks, artists, albums and sets") { nav.search("") }
                if (summary != null) Column(Modifier.clickable { nav.tracks("lately", "") }.padding(top = Ab.space.s)) {
                    RecencyBar(summary.recency)
                    RecencyKey(summary.recency)
                }
                s.error?.let { AbText(it, style = Ab.type.caption, color = Ab.colors.no) }
            }
        }
        if (summary == null) {
            if (!s.loading && s.error == null) item(key = "empty") { EmptyState("Nothing here yet", "Connect to the server in Settings.") }
            return@LazyColumn
        }
        if (summary.recent.isNotEmpty()) item(key = "recent") {
            Column(Modifier.padding(top = Ab.space.xxl)) {
                SectionHead("Now · last 45 days", Ab.colors.ageNow, "You've been in lately", "The albums and sets you've played most in the last 45 days, and your busiest context")
                LazyRow(horizontalArrangement = Arrangement.spacedBy(Ab.space.m)) {
                    items(summary.recent, key = { "${it.kind}-${it.id}" }) { shelf -> ShelfCard(shelf) { open(shelf, nav) } }
                }
            }
        }
        if (summary.languages.isNotEmpty()) item(key = "languages") {
            Column(Modifier.padding(top = Ab.space.xxl)) {
                SectionHead("", Ab.colors.textMuted, "By language", "Tracks by artists who mostly release in each, as MusicBrainz has them")
                Column(verticalArrangement = Arrangement.spacedBy(Ab.space.s)) {
                    summary.languages.chunked(2).forEach { pair ->
                        Row(horizontalArrangement = Arrangement.spacedBy(Ab.space.s)) {
                            pair.forEach { l -> Tile(l.name, "${count(l.played)} of ${count(l.tracks)} played", Modifier.weight(1f)) { nav.language(l.code) } }
                            if (pair.size == 1) Spacer(Modifier.weight(1f))
                        }
                    }
                }
            }
        }
        item(key = "browse") {
            Column(Modifier.padding(top = Ab.space.xxl)) {
                SectionHead("", Ab.colors.textMuted, "Browse", "")
                val r = summary.recency
                val doors = listOf(
                    Triple("Tracks", "${count(summary.tracks)} · ${count(r["lately"] ?: 0)} lately") { nav.tracks("lately", "") },
                    Triple("Artists", "${count(summary.artists)} · by this year") { nav.artists("") },
                    Triple("Albums", count(summary.albums)) { nav.albums("") },
                    Triple("Sets", "${count((summary.sets["played"] ?: 0) + (summary.sets["saved"] ?: 0))} yours · ${count(summary.sets["new"] ?: 0)} new") { nav.sets("") },
                    Triple("Map", "${summary.families} families · ${summary.contexts} contexts") { nav.map() },
                    Triple("Never played", "${count(r["never"] ?: 0)} to try") { nav.tracks("never", "") },
                )
                Column(verticalArrangement = Arrangement.spacedBy(Ab.space.s)) {
                    doors.chunked(2).forEach { pair ->
                        Row(horizontalArrangement = Arrangement.spacedBy(Ab.space.s)) {
                            pair.forEach { (title, detail, onClick) -> Tile(title, detail, Modifier.weight(1f), onClick = onClick) }
                        }
                    }
                }
            }
        }
    }
}

private fun open(shelf: Shelf, nav: CatalogueNav) = when (shelf.kind) {
    "album" -> nav.album(shelf.id)
    "set" -> nav.set(shelf.id)
    else -> nav.context(shelf.id)
}

/** Something you've been in lately: its colours (its tracks' contexts), what kind it is, its name and how much. */
@Composable
private fun ShelfCard(shelf: Shelf, onOpen: () -> Unit) {
    Column(Modifier.width(128.dp).clickable(onClick = onOpen), verticalArrangement = Arrangement.spacedBy(Ab.space.xs)) {
        ListSwatch(shelf.hues.map { it.track() }.ifEmpty { listOf(null) }, size = 128.dp)
        Overline(shelf.kind, color = Ab.colors.textMuted)
        AbText(shelf.title, style = Ab.type.label, maxLines = 2)
        AbText(shelf.detail, style = Ab.type.caption, color = Ab.colors.textMuted, maxLines = 2, modifier = Modifier.fillMaxWidth())
    }
}
