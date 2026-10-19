package com.georgewaller.audiobrain.pages

import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.georgewaller.audiobrain.AppViewModel
import com.georgewaller.audiobrain.ItemRow
import com.georgewaller.audiobrain.MixRow
import com.georgewaller.audiobrain.data.ListItem
import com.georgewaller.audiobrain.design.AbTextField
import com.georgewaller.audiobrain.shortName
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import com.georgewaller.audiobrain.data.MixList
import com.georgewaller.audiobrain.design.Ab
import com.georgewaller.audiobrain.design.AbText
import com.georgewaller.audiobrain.design.Dot
import com.georgewaller.audiobrain.design.EmptyState
import com.georgewaller.audiobrain.design.Overline
import com.georgewaller.audiobrain.design.Spinner
import com.georgewaller.audiobrain.design.contextColor

/** Your playlists: radios and asked mixes first, then the rest grouped by family, each family under its colour. */
@Composable
fun ListsPage(vm: AppViewModel, padding: PaddingValues, onOpenList: (String) -> Unit, onMenu: (ListItem, String) -> Unit) {
    val state by vm.lists.collectAsStateWithLifecycle()
    val verdicts by vm.verdicts.collectAsStateWithLifecycle()
    var query by rememberSaveable { mutableStateOf("") }
    val all = state.lists.filter { it.section == "lists" }
    val words = query.lowercase().split(" ").filter { it.isNotBlank() }
    fun matches(vararg text: String) = words.all { w -> text.any { w in it.lowercase() } }
    val mixes = if (words.isEmpty()) all else all.filter { matches(it.title, it.subtitle) }
    // in search, tracks inside your lists that match, each saying which list it's in
    val found = if (words.isEmpty()) emptyList() else all.flatMap { l -> l.tracks.filter { matches(it.title, it.artist, it.album) }.map { it.copy(reason = "in ${shortName(l.title)}") } }
        .distinctBy { it.trackId }.take(40)
    val yours = mixes.filter { it.kind in setOf("radio", "asked", "explore", "interest", "set") }
    // one group per family, marked with its first context's colour; Fresh and Rediscovery last
    val groups: List<Pair<Pair<String, Float?>, List<MixList>>> = mixes.filter { it !in yours }
        .groupBy { l -> l.context?.family?.takeIf { it.isNotBlank() } ?: if (l.kind == "year") "Your years" else "From your listening" }
        .map { (family, ls) -> (family to ls.firstNotNullOfOrNull { it.context?.hue }) to ls }
        .sortedBy { (k, _) -> if (k.second == null) 1 else 0 }

    LazyColumn(Modifier.fillMaxSize(), contentPadding = padding) {
        item(key = "title") {
            Row(Modifier.padding(top = Ab.space.s, bottom = Ab.space.s), verticalAlignment = Alignment.CenterVertically) {
                AbText("Lists", style = Ab.type.display, modifier = Modifier.weight(1f))
                val synced = all.count { it.sync }
                if (synced > 0) AbText("$synced in Tidal", style = Ab.type.number, color = Ab.colors.keep, modifier = Modifier.padding(end = Ab.space.s))
                if (state.loading) Spinner(size = 14.dp)
            }
        }
        item(key = "search") {
            AbTextField(query, { query = it }, "Search your lists", placeholder = "A list, or a track in one")
        }
        if (found.isNotEmpty()) {
            item(key = "found") { GroupHeader("Tracks in your lists", null) }
            items(found, key = { "f-${it.trackId}" }) { t -> ItemRow(t, verdicts[t.ref.key], { v -> vm.give(v, t, "lists search") }, { onMenu(t, "lists search") }) }
        }
        if (mixes.isEmpty() && words.isEmpty()) item(key = "empty") {
            EmptyState(if (state.loading) "Fetching your lists" else "No lists yet", state.error ?: "They're built by the Mixes job each morning; run it from More › Jobs.")
        }
        if (yours.isNotEmpty()) {
            item(key = "yours") { GroupHeader("Yours", null) }
            items(yours, key = { it.key }) { l -> MixRow(l) { onOpenList(l.key) } }
        }
        groups.forEach { (group, ls) ->
            item(key = "g-${group.first}") { GroupHeader(group.first, group.second) }
            items(ls, key = { it.key }) { l -> MixRow(l) { onOpenList(l.key) } }
        }
        if (state.error != null && mixes.isNotEmpty()) item(key = "error") {
            AbText("Showing saved lists: ${state.error}", style = Ab.type.caption, color = Ab.colors.textFaint, modifier = Modifier.padding(top = Ab.space.m))
        }
    }
}

@Composable
fun GroupHeader(name: String, hue: Float?) {
    Column(Modifier.padding(top = Ab.space.xl, bottom = Ab.space.xs)) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            if (hue != null) {
                Dot(contextColor(hue), size = 8.dp)
                Spacer(Modifier.width(Ab.space.s))
            }
            Overline(name, color = Ab.colors.textMuted)
        }
        Spacer(Modifier.height(Ab.space.xs))
    }
}
