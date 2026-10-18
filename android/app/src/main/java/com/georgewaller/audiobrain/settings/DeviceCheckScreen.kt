package com.georgewaller.audiobrain.settings

import android.content.ClipData
import android.content.ClipboardManager
import android.content.Intent
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.text.selection.SelectionContainer
import androidx.compose.foundation.verticalScroll
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.georgewaller.audiobrain.design.Ab
import com.georgewaller.audiobrain.design.AbButton
import com.georgewaller.audiobrain.design.AbSurface
import com.georgewaller.audiobrain.design.AbText
import com.georgewaller.audiobrain.design.ButtonKind
import com.georgewaller.audiobrain.design.Overline
import com.georgewaller.audiobrain.player.DeviceCheck
import com.georgewaller.audiobrain.player.Players
import com.georgewaller.audiobrain.player.SessionTracker
import kotlinx.coroutines.launch

/** Phase 0 on the device: run it with Tidal playing, then with UAPP playing, and send the report back. */
@Composable
fun DeviceCheckScreen(contentPadding: PaddingValues, onMessage: (String) -> Unit) {
    val context = LocalContext.current
    val scope = rememberCoroutineScope()
    val np by SessionTracker.nowPlaying.collectAsStateWithLifecycle()
    var report by remember { mutableStateOf("") }
    var extra by remember { mutableStateOf("") }
    var busy by remember { mutableStateOf(false) }
    LaunchedEffect(Unit) { report = DeviceCheck.report(context) }
    val full = (report + "\n" + extra).trim()

    Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(contentPadding)) {
        Overline("Phase 0", Modifier.padding(top = Ab.space.l))
        AbText("Device check", style = Ab.type.display)
        Spacer(Modifier.height(Ab.space.s))
        AbText(
            "Start a track in Tidal and run the check; then the same in UAPP. The media browser and play-from-search tries show whether the app can start a specific track itself.",
            style = Ab.type.caption, color = Ab.colors.textMuted,
        )
        Spacer(Modifier.height(Ab.space.l))
        FlowRow(horizontalArrangement = Arrangement.spacedBy(Ab.space.s), verticalArrangement = Arrangement.spacedBy(Ab.space.s)) {
            AbButton("Run check", { SessionTracker.refresh(); report = DeviceCheck.report(context) })
            AbButton(if (busy) "Trying…" else "Try media browsers", {
                busy = true
                scope.launch { extra += "\n" + DeviceCheck.browsers(context); busy = false }
            }, kind = ButtonKind.Quiet, enabled = !busy)
            val playing = np?.takeIf { it.hasTrack }
            AbButton("Play from search: Tidal", {
                val line = if (playing == null) "Play something first; the search uses its artist and title"
                else DeviceCheck.playFromSearch(context, Players.TIDAL, playing.artist, playing.title)
                extra += "\n## Play from search\n$line"
            }, kind = ButtonKind.Quiet)
            AbButton("…UAPP", {
                val line = if (playing == null) "Play something first" else DeviceCheck.playFromSearch(context, Players.UAPP, playing.artist, playing.title)
                extra += "\n## Play from search\n$line"
            }, kind = ButtonKind.Quiet)
        }
        Spacer(Modifier.height(Ab.space.s))
        FlowRow(horizontalArrangement = Arrangement.spacedBy(Ab.space.s)) {
            AbButton("Copy", {
                context.getSystemService(ClipboardManager::class.java).setPrimaryClip(ClipData.newPlainText("Device check", full))
                onMessage("Report copied")
            }, kind = ButtonKind.Ghost)
            AbButton("Share", {
                context.startActivity(Intent.createChooser(Intent(Intent.ACTION_SEND).setType("text/plain").putExtra(Intent.EXTRA_TEXT, full), "Send the report"))
            }, kind = ButtonKind.Ghost)
        }
        Spacer(Modifier.height(Ab.space.l))
        AbSurface(Modifier.fillMaxWidth(), color = Ab.colors.surface, shape = Ab.shapes.medium) {
            SelectionContainer {
                AbText(full, style = Ab.type.code, color = Ab.colors.textMuted, modifier = Modifier.horizontalScroll(rememberScrollState()).padding(Ab.space.m))
            }
        }
        Spacer(Modifier.height(Ab.space.xxl))
    }
}
