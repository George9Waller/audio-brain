package com.georgewaller.audiobrain.settings

import android.Manifest
import android.content.ClipboardManager
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build
import android.provider.Settings as AndroidSettings
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.unit.dp
import androidx.core.content.ContextCompat
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.georgewaller.audiobrain.BuildConfig
import com.georgewaller.audiobrain.data.ApiException
import com.georgewaller.audiobrain.data.DataGraph
import com.georgewaller.audiobrain.data.FeedbackEntity
import com.georgewaller.audiobrain.data.SyncScheduler
import com.georgewaller.audiobrain.data.Verdict
import com.georgewaller.audiobrain.design.Ab
import com.georgewaller.audiobrain.design.AbButton
import com.georgewaller.audiobrain.design.AbSwitch
import com.georgewaller.audiobrain.design.AbText
import com.georgewaller.audiobrain.design.AbTextField
import com.georgewaller.audiobrain.design.ButtonKind
import com.georgewaller.audiobrain.design.Dot
import com.georgewaller.audiobrain.design.SectionHeader
import com.georgewaller.audiobrain.design.SettingRow
import com.georgewaller.audiobrain.design.StatusPill
import com.georgewaller.audiobrain.parseConnectLink
import com.georgewaller.audiobrain.style
import com.georgewaller.audiobrain.player.NowPlayingListener
import com.georgewaller.audiobrain.player.SessionTracker
import com.georgewaller.audiobrain.relative
import kotlinx.coroutines.launch
import java.time.Instant

@Composable
fun SettingsScreen(graph: DataGraph, contentPadding: PaddingValues, onDeviceCheck: () -> Unit, onCatalog: () -> Unit, onMessage: (String) -> Unit) {
    val context = LocalContext.current
    val scope = rememberCoroutineScope()
    val settings by graph.settings.settings.collectAsStateWithLifecycle(null)
    val listening by SessionTracker.connected.collectAsStateWithLifecycle()
    val pending by graph.feedback.pendingCount().collectAsStateWithLifecycle(0)
    val recent by graph.feedback.recent(12).collectAsStateWithLifecycle(emptyList())
    var server by remember { mutableStateOf("") }
    var token by remember { mutableStateOf("") }
    var testing by remember { mutableStateOf(false) }
    var result by remember { mutableStateOf<Pair<Boolean, String>?>(null) }
    var notificationsAllowed by remember {
        mutableStateOf(Build.VERSION.SDK_INT < 33 || ContextCompat.checkSelfPermission(context, Manifest.permission.POST_NOTIFICATIONS) == PackageManager.PERMISSION_GRANTED)
    }
    val askNotifications = rememberLauncherForActivityResult(ActivityResultContracts.RequestPermission()) { notificationsAllowed = it }

    LaunchedEffect(settings?.serverUrl, settings?.token) {
        settings?.let { if (server.isEmpty()) server = it.serverUrl; if (token.isEmpty()) token = it.token }
    }

    fun test() {
        testing = true
        result = null
        scope.launch {
            graph.settings.setConnection(server, token)
            result = try {
                val health = graph.api.health()
                SyncScheduler.requestNow(context)
                true to "Connected${if (health.workerAlive) "; the job worker is running" else "; the job worker isn't running"}"
            } catch (e: ApiException) {
                false to (e.message ?: "Didn't work")
            }
            testing = false
        }
    }

    /** Fills both fields from a pasted connect link and tests it; false when [text] has no link in it. */
    fun useLink(text: String): Boolean {
        val (url, key) = parseConnectLink(text) ?: return false
        server = url
        token = key
        test()
        return true
    }

    fun pasteLink() {
        val clip = context.getSystemService(ClipboardManager::class.java).primaryClip
        val text = clip?.takeIf { it.itemCount > 0 }?.getItemAt(0)?.coerceToText(context)?.toString().orEmpty()
        if (!useLink(text)) onMessage("No connect link on the clipboard")
    }

    Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(contentPadding)) {
        AbText("Settings", style = Ab.type.display, modifier = Modifier.padding(top = Ab.space.l))

        SectionHeader("Server")
        // a pasted connect link (much text at once, not typing) fills both fields
        AbTextField(server, { if (it.length - server.length < 20 || !useLink(it)) server = it }, "Address", placeholder = "http://audio-brain.tailnet.ts.net:8000", keyboardType = KeyboardType.Uri)
        Spacer(Modifier.height(Ab.space.m))
        AbTextField(token, { token = it }, "Token", placeholder = "from manage.py api_token", secret = true)
        Spacer(Modifier.height(Ab.space.m))
        Row(verticalAlignment = Alignment.CenterVertically) {
            AbButton(if (testing) "Testing…" else "Save and test", ::test, enabled = !testing && server.isNotBlank() && token.isNotBlank())
            Spacer(Modifier.width(Ab.space.m))
            result?.let { (ok, text) -> AbText(text, style = Ab.type.caption, color = if (ok) Ab.colors.keep else Ab.colors.no, modifier = Modifier.weight(1f)) }
        }
        Spacer(Modifier.height(Ab.space.m))
        AbButton("Paste connect link", ::pasteLink, kind = ButtonKind.Quiet)
        AbText("Or copy the link `manage.py api_token` prints, then paste it here or into Address.", style = Ab.type.caption, color = Ab.colors.textFaint, modifier = Modifier.padding(top = Ab.space.s))

        SectionHeader("Listening")
        SettingRow(
            "Notification access", description = if (listening) "Following what's playing in any player" else "Needed to see what Tidal or UAPP is playing",
            onClick = { context.startActivity(Intent(AndroidSettings.ACTION_NOTIFICATION_LISTENER_SETTINGS).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)) },
        ) { StatusPill(if (listening) "on" else "off", if (listening) Ab.colors.keep else Ab.colors.warning) }
        if (!notificationsAllowed) {
            SettingRow("Allow notifications", description = "For Keep and No on the lock screen") {
                AbButton("Allow", { askNotifications.launch(Manifest.permission.POST_NOTIFICATIONS) }, kind = ButtonKind.Quiet)
            }
        }
        SettingRow("Feedback notification", description = "Keep, No and Never while something plays; works offline") {
            AbSwitch(settings?.feedbackNotification ?: true, { on -> scope.launch { graph.settings.setFeedbackNotification(on) } })
        }
        val playOn = settings?.playOn ?: com.georgewaller.audiobrain.data.Settings.TIDAL
        SettingRow("Play tracks in", description = "Where Play on a track starts it") {
            Row(horizontalArrangement = Arrangement.spacedBy(Ab.space.xs)) {
                listOf(com.georgewaller.audiobrain.data.Settings.TIDAL, com.georgewaller.audiobrain.data.Settings.YT_MUSIC).forEach { pkg ->
                    com.georgewaller.audiobrain.design.AbChip(com.georgewaller.audiobrain.playerName(pkg), selected = playOn == pkg) { scope.launch { graph.settings.setPlayOn(pkg) } }
                }
            }
        }

        SectionHeader("Outbox") {
            AbText(if (pending == 0) "all sent" else "$pending waiting", style = Ab.type.caption, color = Ab.colors.textMuted)
        }
        recent.forEach { OutboxRow(it) }
        if (recent.isEmpty()) AbText("Verdicts you give appear here until they're on the server.", style = Ab.type.caption, color = Ab.colors.textFaint)
        Spacer(Modifier.height(Ab.space.m))
        AbButton("Sync now", { SyncScheduler.requestNow(context); onMessage("Syncing when the server is reachable") }, kind = ButtonKind.Quiet)

        SectionHeader("Tools")
        SettingRow("Device check", description = "What this device and its players allow; copy the report", onClick = onDeviceCheck)
        SettingRow("Design system", description = "Every component and token", onClick = onCatalog)
        SettingRow("Re-read media sessions", description = "If now playing looks stuck", onClick = { NowPlayingListener.requestRebind(context); SessionTracker.refresh() })

        AbText("Audio Brain ${BuildConfig.VERSION_NAME}", style = Ab.type.caption, color = Ab.colors.textFaint, modifier = Modifier.padding(vertical = Ab.space.xl))
    }
}

@Composable
private fun OutboxRow(item: FeedbackEntity) {
    val verdict = Verdict.fromApi(item.verdict)
    Row(Modifier.fillMaxWidth().padding(vertical = 6.dp), verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(Ab.space.m)) {
        Dot(verdict?.style()?.color() ?: Ab.colors.textFaint, size = 9.dp)
        Column(Modifier.weight(1f)) {
            AbText("${item.title} · ${item.artist}", style = Ab.type.label, maxLines = 1)
            AbText(
                "${verdict?.style()?.label ?: item.verdict} · ${item.where} · ${relative(Instant.ofEpochMilli(item.givenAt))}${item.error?.let { " · $it" } ?: ""}",
                style = Ab.type.caption, color = Ab.colors.textFaint, maxLines = 1,
            )
        }
        when (item.state) {
            FeedbackEntity.SENT -> StatusPill("sent", Ab.colors.keep)
            FeedbackEntity.REJECTED -> StatusPill("rejected", Ab.colors.no)
            else -> StatusPill("waiting", Ab.colors.notNow)
        }
    }
}

