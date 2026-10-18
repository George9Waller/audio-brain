package com.georgewaller.audiobrain.overlays

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.itemsIndexed
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.text.BasicTextField
import androidx.compose.foundation.verticalScroll
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.focus.focusRequester
import androidx.compose.ui.graphics.SolidColor
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.georgewaller.audiobrain.AppViewModel
import com.georgewaller.audiobrain.ItemRow
import com.georgewaller.audiobrain.MixRow
import com.georgewaller.audiobrain.design.Ab
import com.georgewaller.audiobrain.design.AbButton
import com.georgewaller.audiobrain.design.AbChip
import com.georgewaller.audiobrain.design.AbSurface
import com.georgewaller.audiobrain.design.AbText
import com.georgewaller.audiobrain.design.ActionRow
import com.georgewaller.audiobrain.design.BuildingAnimation
import com.georgewaller.audiobrain.design.ButtonKind
import com.georgewaller.audiobrain.design.SectionHeader

/** Auto lets the prompt decide ("two hours of…"), else 30 tracks; about 15 tracks an hour. */
private val LENGTHS = listOf<Pair<String, Int?>>("Auto" to null, "1 h" to 15, "2 h" to 30, "3 h" to 45)

/** Write what you want; the quick actions are there as starting points, recent asks below. */
@Composable
fun AskScreen(vm: AppViewModel, prefill: String, padding: PaddingValues, onOpenList: (String) -> Unit) {
    var text by rememberSaveable { mutableStateOf(prefill) }
    var length by rememberSaveable { mutableIntStateOf(0) }
    val actions by vm.actions.collectAsStateWithLifecycle()
    val lists by vm.lists.collectAsStateWithLifecycle()
    val focus = remember { FocusRequester() }
    LaunchedEffect(Unit) { runCatching { focus.requestFocus() } }

    Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(padding), verticalArrangement = Arrangement.spacedBy(Ab.space.m)) {
        AbText("Ask", style = Ab.type.display, modifier = Modifier.padding(top = Ab.space.s))
        AbSurface(Modifier.fillMaxWidth(), color = Ab.colors.ground, shape = Ab.shapes.medium, border = Ab.colors.textFaint) {
            Box(Modifier.padding(Ab.space.l).heightIn(min = 120.dp)) {
                if (text.isEmpty()) AbText("Upbeat house with vocals for cooking…", style = Ab.type.body.copy(fontSize = Ab.type.heading.fontSize), color = Ab.colors.textFaint)
                BasicTextField(
                    text, { text = it }, Modifier.fillMaxWidth().focusRequester(focus),
                    textStyle = Ab.type.body.copy(color = Ab.colors.text, fontSize = Ab.type.heading.fontSize, lineHeight = Ab.type.title.lineHeight),
                    cursorBrush = SolidColor(Ab.colors.text),
                )
            }
        }
        Row(horizontalArrangement = Arrangement.spacedBy(Ab.space.s), verticalAlignment = Alignment.CenterVertically) {
            AbText("Length", style = Ab.type.number, color = Ab.colors.textFaint)
            LENGTHS.forEachIndexed { i, (label, _) -> AbChip(label, selected = i == length) { length = i } }
            Spacer(Modifier.weight(1f))
            AbButton("Make it", {
                // "explore Ólafur Arnalds" builds an Explore list; anything else is a prompt
                val artist = Regex("^\\s*explore\\s+(.+)$", RegexOption.IGNORE_CASE).find(text)?.groupValues?.get(1)?.trim()
                if (artist != null) vm.explore(name = artist) else vm.makeFromPrompt(text, LENGTHS[length].second)
            }, enabled = text.isNotBlank())
        }
        if (actions.isNotEmpty()) {
            SectionHeader("Or start from")
            actions.forEach { a -> ActionRow(a.label, a.detail) { vm.makeFromAction(a) } }
        }
        val asked = lists.lists.filter { it.kind == "asked" }
        if (asked.isNotEmpty()) {
            SectionHeader("Recent asks")
            asked.take(5).forEach { l -> MixRow(l) { onOpenList(l.key) } }
        }
        Spacer(Modifier.height(Ab.space.xl))
    }
}

/** While the server makes the mix: your context colours gathering, a line stepping through what it does. */
@Composable
fun BuildingScreen(vm: AppViewModel, building: AppViewModel.Making.Building, padding: PaddingValues) {
    val lists by vm.lists.collectAsStateWithLifecycle()
    val hues = remember(lists.lists.size) { lists.lists.flatMap { l -> l.tracks.mapNotNull { it.hue } }.distinct().shuffled().take(28).ifEmpty { (0 until 28).map { it * 13f } } }
    Column(Modifier.fillMaxSize().padding(padding), horizontalAlignment = Alignment.CenterHorizontally) {
        AbText(building.title, style = Ab.type.title, maxLines = 2, modifier = Modifier.padding(top = Ab.space.s).fillMaxWidth())
        Spacer(Modifier.weight(1f))
        BuildingAnimation(hues, building.stages)
        Spacer(Modifier.weight(1f))
        AbButton("Cancel", vm::cancelMaking, kind = ButtonKind.Ghost)
        Spacer(Modifier.height(Ab.space.xl))
    }
}

/** The result, for review: a normal list marked as a draft. Nothing is saved until you keep or play it. */
@Composable
fun DraftScreen(vm: AppViewModel, draft: AppViewModel.Making.Draft, padding: PaddingValues, onRefine: (String) -> Unit, onMenu: (com.georgewaller.audiobrain.data.ListItem, String) -> Unit) {
    val verdicts by vm.verdicts.collectAsStateWithLifecycle()
    val items = draft.mix.items.filter { it.trackId !in draft.removed }
    LazyColumn(Modifier.fillMaxSize(), contentPadding = padding) {
        item(key = "bar") {
            AbSurface(Modifier.fillMaxWidth().padding(top = Ab.space.s), color = Ab.colors.ground, shape = Ab.shapes.small, border = Ab.colors.textFaint) {
                AbText("Draft · not saved until you keep it", style = Ab.type.number, color = Ab.colors.textMuted, modifier = Modifier.padding(Ab.space.m))
            }
        }
        item(key = "head") {
            ListHeader(draft.mix.copy(items = items, subtitle = "Read as: ${draft.understood}")) {
                Row(horizontalArrangement = Arrangement.spacedBy(Ab.space.s)) {
                    AbButton("Keep", vm::keepDraft)
                    AbButton("Play", { vm.play(draft.mix.key) }, kind = ButtonKind.Quiet)
                    AbButton("Refine", { onRefine(draft.prompt) }, kind = ButtonKind.Ghost)
                    AbButton("Discard", vm::discardDraft, kind = ButtonKind.Ghost)
                }
            }
        }
        if (draft.removed.isNotEmpty()) item(key = "removed") {
            AbText("${draft.removed.size} swiped out; they'll be left off when you keep it", style = Ab.type.caption, color = Ab.colors.textFaint, modifier = Modifier.padding(bottom = Ab.space.s))
        }
        itemsIndexed(items, key = { i, it -> "d-$i-${it.trackId}" }) { _, item ->
            ItemRow(
                item, verdicts[item.ref.key], { v -> vm.give(v, item, "draft") }, { onMenu(item, "draft") },
                onSwipeLeft = { vm.removeFromDraft(item) }, leftLabel = "Remove",
            )
        }
    }
}
