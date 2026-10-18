package com.georgewaller.audiobrain.design

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.tooling.preview.Preview
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp

private val HOUSE = SigilSpec(listOf(0.28f, 0.87f, 0.99f, 0.2f, 0.11f, 0.31f), 0.61f, 132f, 173.7f)
private val BALLAD = SigilSpec(listOf(0.2f, 0.05f, 0.3f, 0.05f, 0.6f, 0.9f), 0.2f, 84f, 356f)

/** Every component in its states, on one screen: the design system's own reference, and a quick check on the DAP. */
@Composable
fun DesignCatalog(modifier: Modifier = Modifier) {
    var verdict by remember { mutableStateOf<VerdictStyle?>(VerdictStyle.Keep) }
    var switch by remember { mutableStateOf(true) }
    var tab by remember { mutableIntStateOf(0) }
    var text by remember { mutableStateOf("") }
    val c = Ab.colors
    Column(modifier.fillMaxSize().background(c.ground).verticalScroll(rememberScrollState()).padding(horizontal = Ab.space.gutter)) {
        SectionHeader("Colour")
        FlowRow(horizontalArrangement = Arrangement.spacedBy(Ab.space.s), verticalArrangement = Arrangement.spacedBy(Ab.space.s)) {
            listOf(c.ground, c.surface, c.raised, c.overlay, c.line, c.text, c.textMuted, c.textFaint, c.keep, c.no, c.notNow, c.never).forEach { Swatch(it) }
        }
        Spacer(Modifier.height(Ab.space.s))
        ColourStrip((0 until 36).map { if (it % 7 == 6) null else it * 10f })
        SectionHeader("Type")
        AbText("Home", style = Ab.type.display)
        AbText("Disco: Earth, Wind & Fire (2016–2018)", style = Ab.type.title)
        AbText("House: Dom Dolla", style = Ab.type.heading)
        AbText("Body copy for reasons and descriptions, which can run to two lines on a small screen.", style = Ab.type.body)
        AbText("Label", style = Ab.type.label)
        AbText("Caption · 3 min ago", style = Ab.type.caption, color = c.textMuted)
        Overline("Overline · 8A · 124.0")
        SectionHeader("Sigils")
        Row(horizontalArrangement = Arrangement.spacedBy(Ab.space.l)) {
            SoundSigil(HOUSE, size = 96.dp, playing = true)
            SoundSigil(BALLAD, size = 56.dp)
            SoundSigil(SigilSpec.fromSeed("Unknown - Track"), size = 56.dp, faint = true)
        }
        SectionHeader("Ask")
        AskBox("What do you want to hear?") {}
        ActionRow("More like Love Songs", "Sounds and plays like it · Prospa") {}
        SectionHeader("Rows")
        TrackRow("Darkness", "CHRIS STASSY · New · one of your House: Ben Hemsley tracks", sigil = HOUSE, facts = "132\n6A", onSwipeRight = {}, onSwipeLeft = {})
        TrackRow("I Know Where I've Been", "Queen Latifah · New to you", sigil = BALLAD, facts = "84\n3A", verdict = VerdictStyle.Keep, onSwipeRight = {}, onSwipeLeft = {})
        TrackRow("An unanalysed track", "Somebody · 2 plays", sigil = SigilSpec.fromSeed("x"), sigilFaint = true, verdict = VerdictStyle.No)
        AbDivider()
        ListRow("House: Dom Dolla", "40 · 35% new", (0 until 40).map { if (it % 5 == 0) null else 173.7f }, synced = true) {}
        ListRow("Rediscovery", "40 · 0% new", (0 until 40).map { (it * 37f) % 360 }) {}
        SectionHeader("Verdicts")
        VerdictBar(verdict, { verdict = if (verdict == it) null else it })
        SectionHeader("Controls")
        SegmentTabs(listOf("New", "Rediscover", "Explore"), tab, { tab = it })
        PageLabels(listOf("Lists", "Home", "Discover", "Map", "More"), 1.3f, alerts = setOf(4)) {}
        Row(horizontalArrangement = Arrangement.spacedBy(Ab.space.s)) {
            AbButton("Keep", {})
            AbButton("Play in Tidal", {}, kind = ButtonKind.Quiet)
            AbButton("Discard", {}, kind = ButtonKind.Ghost)
        }
        SettingRow("Feedback notification", description = "Keep and No from the lock screen") { AbSwitch(switch, { switch = it }) }
        AbTextField(text, { text = it }, "Server", placeholder = "http://audio-brain.tailnet.ts.net:8000")
        FlowRow(horizontalArrangement = Arrangement.spacedBy(Ab.space.s), verticalArrangement = Arrangement.spacedBy(Ab.space.s), modifier = Modifier.padding(top = Ab.space.m)) {
            StatusPill("running", c.keep, live = true)
            StatusPill("failed", c.no)
            StatusPill("queued", c.notNow)
            Pips(0.75f)
            LevelBar(0.6f)
        }
        SectionHeader("Chip")
        NowChip("Love Songs (feat. Kosmo Kint)", "Prospa · Tidal", HOUSE, playing = true, syncColor = c.notNow) {}
        SectionHeader("Building")
        BuildingAnimation((0 until 28).map { it * 13f }, listOf("Reading your request" to "sounds like “upbeat house”", "Ordering" to "an energy arc"))
        Spacer(Modifier.height(Ab.space.xxl))
    }
}

@Composable
private fun Swatch(color: Color, size: Dp = 28.dp) {
    Box(Modifier.size(size).background(color, Ab.shapes.small))
}

@Preview(widthDp = 360, heightDp = 2000)
@Composable
private fun CatalogPreview() = AbTheme { DesignCatalog() }
