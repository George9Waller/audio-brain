package com.georgewaller.audiobrain.pages

import androidx.compose.animation.core.animateFloatAsState
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.rotate
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.georgewaller.audiobrain.AppViewModel
import com.georgewaller.audiobrain.ItemRow
import com.georgewaller.audiobrain.ListAge
import com.georgewaller.audiobrain.MixRow
import com.georgewaller.audiobrain.byAge
import com.georgewaller.audiobrain.meta
import com.georgewaller.audiobrain.shown
import com.georgewaller.audiobrain.tag
import com.georgewaller.audiobrain.design.AbChip
import com.georgewaller.audiobrain.design.AbIcon
import com.georgewaller.audiobrain.design.AbIcons
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

/**
 * Your playlists by how recent they are: yours first, then the contexts you're playing now, older eras, lists kept
 * from retired contexts, new music, habits and your years. Each row is tagged with its age.
 */
@Composable
fun ListsPage(vm: AppViewModel, padding: PaddingValues, onOpenList: (String) -> Unit, onMenu: (ListItem, String) -> Unit) {
    val state by vm.lists.collectAsStateWithLifecycle()
    val verdicts by vm.verdicts.collectAsStateWithLifecycle()
    var query by rememberSaveable { mutableStateOf("") }
    var opened by rememberSaveable { mutableStateOf(emptyList<String>()) }  // sections showing all their lists
    val all = state.lists.filter { it.section == "lists" }
    val words = query.lowercase().split(" ").filter { it.isNotBlank() }
    fun matches(vararg text: String) = words.all { w -> text.any { w in it.lowercase() } }
    val mixes = if (words.isEmpty()) all else all.filter { matches(it.title, it.subtitle) }
    // in search, tracks inside your lists that match, each saying which list it's in
    val found = if (words.isEmpty()) emptyList() else all.flatMap { l -> l.tracks.filter { matches(it.title, it.artist, it.album) }.map { it.copy(reason = "in ${shortName(l.title)}") } }
        .distinctBy { it.trackId }.take(40)
    val sections = byAge(mixes)

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
        sections.forEach { (age, ls) ->
            // a long section shows its first few, unless opened from its header or searching
            val long = words.isEmpty() && ls.size > (age.shown ?: Int.MAX_VALUE)
            val open = age.name in opened
            item(key = "s-${age.name}") {
                AgeHeader(age, if (long) ls.size else null, open) { opened = if (open) opened - age.name else opened + age.name }
            }
            if (age == ListAge.Year) item(key = "years") {
                FlowRow(horizontalArrangement = Arrangement.spacedBy(Ab.space.s), verticalArrangement = Arrangement.spacedBy(Ab.space.s)) {
                    ls.forEach { l -> AbChip(l.key.substringAfter(":"), onClick = { onOpenList(l.key) }) }
                }
            }
            else items(if (long && !open) ls.take(age.shown!!) else ls, key = { it.key }) { l -> MixRow(l, age.meta(l), age.tag(l)) { onOpenList(l.key) } }
        }
        if (state.error != null && mixes.isNotEmpty()) item(key = "error") {
            AbText("Showing saved lists: ${state.error}", style = Ab.type.caption, color = Ab.colors.textFaint, modifier = Modifier.padding(top = Ab.space.m))
        }
    }
}

/** A section's name, and a line on what puts a list in it. With [total], the section is longer than it shows: the
 * header opens and closes it, saying how many there are and turning its chevron. */
@Composable
fun AgeHeader(age: ListAge, total: Int? = null, open: Boolean = false, onToggle: () -> Unit = {}) {
    val turn by animateFloatAsState(if (open) 180f else 0f, label = "chevron")
    Row(
        Modifier.fillMaxWidth()
            .then(if (total != null) Modifier.clickable(role = Role.Button, onClickLabel = if (open) "Show fewer" else "Show all", onClick = onToggle) else Modifier)
            .padding(top = Ab.space.xl, bottom = Ab.space.xs),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Column(Modifier.weight(1f)) {
            AbText(age.title, style = Ab.type.title, maxLines = 1)
            AbText(age.caption, style = Ab.type.caption, color = Ab.colors.textMuted, maxLines = 2)
        }
        if (total != null) {
            AbText(if (open) "All $total" else "${age.shown} of $total", style = Ab.type.number, color = Ab.colors.textMuted, modifier = Modifier.padding(start = Ab.space.s))
            AbIcon(AbIcons.Down, Modifier.padding(start = Ab.space.xs).rotate(turn), tint = Ab.colors.textMuted, size = 18.dp)
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
