package com.georgewaller.audiobrain.pages

import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.ui.Modifier
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.georgewaller.audiobrain.AppViewModel
import com.georgewaller.audiobrain.design.Ab
import com.georgewaller.audiobrain.design.AbDivider
import com.georgewaller.audiobrain.design.AbText
import com.georgewaller.audiobrain.design.SettingRow
import com.georgewaller.audiobrain.design.StatusPill

/** Everything that isn't music: the server's jobs, sync, settings and tools. */
@Composable
fun MorePage(vm: AppViewModel, padding: PaddingValues, onJobs: () -> Unit, onSettings: () -> Unit, onDeviceCheck: () -> Unit, onCatalog: () -> Unit) {
    val pending by vm.pending.collectAsStateWithLifecycle()
    val listening by vm.listening.collectAsStateWithLifecycle()
    val settings by vm.settings.collectAsStateWithLifecycle()
    Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(padding)) {
        AbText("More", style = Ab.type.display, modifier = Modifier.padding(top = Ab.space.s, bottom = Ab.space.m))
        SettingRow("Jobs", description = "What the server runs, and when; run one now", onClick = onJobs)
        AbDivider()
        SettingRow("Sync and outbox", description = if (pending == 0) "Everything you've given is on the server" else "$pending waiting to send", onClick = onSettings) {
            if (pending > 0) StatusPill("$pending", Ab.colors.notNow)
        }
        AbDivider()
        SettingRow("Settings", description = if (settings?.configured == true) settings!!.serverUrl else "Connect to your server", onClick = onSettings) {
            if (!listening) StatusPill("no access", Ab.colors.warning)
        }
        AbDivider()
        SettingRow("Device check", description = "What Tidal and UAPP allow on this device", onClick = onDeviceCheck)
        AbDivider()
        SettingRow("Design system", description = "Every component and token", onClick = onCatalog)
    }
}
