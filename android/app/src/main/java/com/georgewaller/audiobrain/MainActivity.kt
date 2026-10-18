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

    override fun onCreate(savedInstanceState: Bundle?) {
        enableEdgeToEdge(SystemBarStyle.dark(android.graphics.Color.TRANSPARENT), SystemBarStyle.dark(android.graphics.Color.TRANSPARENT))
        super.onCreate(savedInstanceState)
        handle(intent)
        setContent {
            AbTheme {
                Shell(dataGraph, openSettings, onSettingsOpened = { openSettings = false })
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
        val uri = intent?.data ?: return
        if (uri.scheme != "audiobrain" || uri.host != "connect") return
        val url = uri.getQueryParameter("url").orEmpty()
        val token = uri.getQueryParameter("token").orEmpty()
        if (url.isBlank() || token.isBlank()) return
        lifecycleScope.launch {
            dataGraph.settings.setConnection(url, token)
            openSettings = true
        }
    }
}
