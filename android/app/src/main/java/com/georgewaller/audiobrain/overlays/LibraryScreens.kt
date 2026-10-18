package com.georgewaller.audiobrain.overlays

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.georgewaller.audiobrain.AppViewModel
import com.georgewaller.audiobrain.ItemRow
import com.georgewaller.audiobrain.data.AlbumPage
import com.georgewaller.audiobrain.data.ApiException
import com.georgewaller.audiobrain.data.ArtistPage
import com.georgewaller.audiobrain.data.DataGraph
import com.georgewaller.audiobrain.data.ListItem
import com.georgewaller.audiobrain.data.SetPage
import com.georgewaller.audiobrain.design.Ab
import com.georgewaller.audiobrain.design.AbButton
import com.georgewaller.audiobrain.design.AbIcons
import com.georgewaller.audiobrain.design.AbText
import com.georgewaller.audiobrain.design.ButtonKind
import com.georgewaller.audiobrain.design.EmptyState
import com.georgewaller.audiobrain.design.Overline
import com.georgewaller.audiobrain.design.SectionHeader
import com.georgewaller.audiobrain.design.TrackRow
import com.georgewaller.audiobrain.openUrl
import kotlin.math.roundToInt

/** Loads a page once, showing a placeholder until it arrives. */
@Composable
private fun <T> Loaded(key: Any, load: suspend () -> T, content: @Composable (T) -> Unit) {
    var value by remember(key) { mutableStateOf<T?>(null) }
    var error by remember(key) { mutableStateOf<String?>(null) }
    LaunchedEffect(key) { try { value = load() } catch (e: ApiException) { error = e.message } }
    val v = value
    if (v == null) EmptyState(if (error != null) "Couldn't load it" else "Loading", error ?: "") else content(v)
}

@Composable
fun ArtistScreen(vm: AppViewModel, graph: DataGraph, id: Long, padding: PaddingValues, onAlbum: (Long) -> Unit, onMenu: (ListItem, String) -> Unit) {
    val verdicts by vm.verdicts.collectAsStateWithLifecycle()
    val context = LocalContext.current
    Loaded<ArtistPage>(id, { graph.api.artistPage(id) }) { page ->
        LazyColumn(Modifier.fillMaxSize(), contentPadding = padding) {
            item(key = "h") {
                Column(Modifier.padding(top = Ab.space.s), verticalArrangement = Arrangement.spacedBy(Ab.space.s)) {
                    Overline("Artist · ${page.artist.tracks} tracks", color = Ab.colors.textMuted)
                    AbText(page.artist.name, style = Ab.type.title, maxLines = 2)
                    Row(horizontalArrangement = Arrangement.spacedBy(Ab.space.s)) {
                        AbButton("Explore", { vm.explore(name = page.artist.name, artistId = page.artist.id) })
                        page.tracks.firstOrNull()?.let { t -> AbButton("Radio", { vm.startRadio(t) }, kind = ButtonKind.Quiet) }
                        page.tidalUrl?.let { url -> AbButton("Open in Tidal", { openUrl(context, url) }, kind = ButtonKind.Ghost, icon = AbIcons.Open) }
                    }
                }
            }
            if (page.albums.isNotEmpty()) {
                item(key = "ah") { SectionHeader("Albums") }
                items(page.albums, key = { "al-${it.id}" }) { a ->
                    TrackRow(a.name, "${a.tracks} ${if (a.tracks == 1) "track" else "tracks"}", facts = "${a.plays.roundToInt()}\nplays", onClick = { onAlbum(a.id) })
                }
            }
            item(key = "th") { SectionHeader("Most played") }
            items(page.tracks, key = { "t-${it.trackId}" }) { t -> ItemRow(t, verdicts[t.ref.key], { v -> vm.give(v, t, "artist") }, { onMenu(t, "artist") }) }
        }
    }
}

@Composable
fun AlbumScreen(vm: AppViewModel, graph: DataGraph, id: Long, padding: PaddingValues, onArtist: (Long) -> Unit, onMenu: (ListItem, String) -> Unit) {
    val verdicts by vm.verdicts.collectAsStateWithLifecycle()
    val context = LocalContext.current
    Loaded<AlbumPage>(id, { graph.api.albumPage(id) }) { page ->
        LazyColumn(Modifier.fillMaxSize(), contentPadding = padding) {
            item(key = "h") {
                Column(Modifier.padding(top = Ab.space.s, bottom = Ab.space.m), verticalArrangement = Arrangement.spacedBy(Ab.space.s)) {
                    Overline("Album · ${page.album.tracks} tracks in your library", color = Ab.colors.textMuted)
                    AbText(page.album.name, style = Ab.type.title, maxLines = 3)
                    AbText(page.album.artist, style = Ab.type.label, color = Ab.colors.textMuted, modifier = Modifier.padding(vertical = Ab.space.xs).let { it })
                    Row(horizontalArrangement = Arrangement.spacedBy(Ab.space.s)) {
                        AbButton("Artist", { onArtist(page.album.artistId) }, kind = ButtonKind.Quiet)
                        page.album.tidalUrl?.let { url -> AbButton("Open in Tidal", { openUrl(context, url) }, kind = ButtonKind.Ghost, icon = AbIcons.Open) }
                    }
                }
            }
            items(page.tracks, key = { "t-${it.trackId}" }) { t -> ItemRow(t, verdicts[t.ref.key], { v -> vm.give(v, t, "album") }, { onMenu(t, "album") }) }
        }
    }
}

/** A set and its tracklist: entries matched to your catalogue are full rows you can rate; the rest are listed plainly. */
@Composable
fun SetScreen(vm: AppViewModel, graph: DataGraph, id: Long, padding: PaddingValues, onMenu: (ListItem, String) -> Unit) {
    val verdicts by vm.verdicts.collectAsStateWithLifecycle()
    val context = LocalContext.current
    Loaded<SetPage>(id, { graph.api.setPage(id) }) { page ->
        LazyColumn(Modifier.fillMaxSize(), contentPadding = padding) {
            item(key = "h") {
                Column(Modifier.padding(top = Ab.space.s, bottom = Ab.space.m), verticalArrangement = Arrangement.spacedBy(Ab.space.s)) {
                    Overline(listOfNotNull("Set", page.set.source, page.set.minutes?.let { "$it min" }).joinToString(" · "), color = Ab.colors.textMuted)
                    AbText(page.set.title, style = Ab.type.title, maxLines = 3)
                    AbText(page.set.by, style = Ab.type.label, color = Ab.colors.textMuted)
                    AbText(
                        if (page.entries.isEmpty()) "No tracklist found yet" else "${page.set.inCatalogue} of ${page.entries.size} tracks are in your catalogue",
                        style = Ab.type.caption, color = Ab.colors.textFaint,
                    )
                    AbButton(if ("youtube" in page.set.url) "Watch on YouTube" else "Find in Tidal", { openUrl(context, page.set.url) }, kind = ButtonKind.Ghost, icon = AbIcons.Open)
                }
            }
            items(page.entries, key = { "e-${it.position}" }) { e ->
                val t = e.track
                Row(verticalAlignment = Alignment.CenterVertically) {
                    AbText(e.cue.ifBlank { "${e.position}" }, style = Ab.type.number, color = Ab.colors.textFaint, modifier = Modifier.width(44.dp))
                    if (t != null) ItemRow(t, verdicts[t.ref.key], { v -> vm.give(v, t, "set:$id") }, { onMenu(t, "set:$id") })
                    else TrackRow(e.title, listOf(e.artist, e.label).filter { it.isNotBlank() }.joinToString(" · "))
                }
            }
        }
    }
}
