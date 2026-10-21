package com.georgewaller.audiobrain

import android.content.Intent
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.SystemBarStyle
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.lifecycle.lifecycleScope
import com.georgewaller.audiobrain.data.dataGraph
import com.georgewaller.audiobrain.design.AbTheme
import com.georgewaller.audiobrain.player.NowPlayingListener
import com.georgewaller.audiobrain.player.SessionTracker
import kotlinx.coroutines.launch

class MainActivity : ComponentActivity() {
    private var openSettings by mutableStateOf(false)
    private var shared by mutableStateOf<String?>(null)
    private var openList by mutableStateOf<String?>(null)

    override fun onCreate(savedInstanceState: Bundle?) {
        enableEdgeToEdge(SystemBarStyle.dark(android.graphics.Color.TRANSPARENT), SystemBarStyle.dark(android.graphics.Color.TRANSPARENT))
        super.onCreate(savedInstanceState)
        handle(intent)
        setContent {
            AbTheme {
                Shell(dataGraph, openSettings, onSettingsOpened = { openSettings = false }, shared = shared, onShareHandled = { shared = null }, openList = openList, onListOpened = { openList = null })
            }
        }
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        handle(intent)
    }

    override fun onResume() {
        super.onResume()
        // back from granting notification access: bind now rather than at the next boot
        if (!SessionTracker.connected.value) NowPlayingListener.requestRebind(this) else SessionTracker.refresh()
    }

    /** audiobrain://connect?url=…&token=… (printed by `manage.py api_token`): save the connection and show Settings. */
    private fun handle(intent: Intent?) {
        intent?.getStringExtra(EXTRA_LIST)?.let { openList = it; return }  // a list tapped on the widget
        if (intent?.action == Intent.ACTION_SEND) {  // shared text or a link: a tracklist (sets.provided on the server)
            val text = listOfNotNull(intent.getStringExtra(Intent.EXTRA_SUBJECT), intent.getStringExtra(Intent.EXTRA_TEXT)).joinToString("\n").trim()
            if (text.isNotBlank()) shared = text
            return
        }
        val (url, token) = intent?.dataString?.let(::parseConnectLink) ?: return
        lifecycleScope.launch {
            dataGraph.settings.setConnection(url, token)
            openSettings = true
        }
    }

    companion object {
        const val EXTRA_LIST = "list"
    }
}
