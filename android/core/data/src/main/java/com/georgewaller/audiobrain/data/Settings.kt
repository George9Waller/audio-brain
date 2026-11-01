package com.georgewaller.audiobrain.data

import android.content.Context
import androidx.datastore.preferences.core.booleanPreferencesKey
import androidx.datastore.preferences.core.edit
import androidx.datastore.preferences.core.stringPreferencesKey
import androidx.datastore.preferences.core.stringSetPreferencesKey
import androidx.datastore.preferences.preferencesDataStore
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.flow.map

private val Context.store by preferencesDataStore("settings")

data class Settings(
    val serverUrl: String = "",
    val token: String = "",
    val feedbackNotification: Boolean = true,
    val playOn: String = TIDAL,  // the player a tapped track starts in: Tidal or YouTube Music's package
    val serverAlerts: Boolean = true,  // a notification when the server warns of failed jobs or stale models
) {
    val configured get() = serverUrl.isNotBlank() && token.isNotBlank()

    companion object {
        const val TIDAL = "com.aspiro.tidal"
        const val YT_MUSIC = "com.google.android.apps.youtube.music"
    }
}

class SettingsStore(private val context: Context) {
    private val server = stringPreferencesKey("server_url")
    private val token = stringPreferencesKey("token")
    private val notification = booleanPreferencesKey("feedback_notification")
    private val playOn = stringPreferencesKey("play_on")
    private val alerts = booleanPreferencesKey("server_alerts")
    private val notified = stringSetPreferencesKey("server_warnings_notified")

    val settings: Flow<Settings> = context.store.data.map {
        Settings(it[server].orEmpty(), it[token].orEmpty(), it[notification] ?: true, it[playOn] ?: Settings.TIDAL, it[alerts] ?: true)
    }

    suspend fun current(): Settings = settings.first()

    suspend fun connection(): Pair<String, String> = current().let { it.serverUrl to it.token }

    suspend fun setConnection(serverUrl: String, token: String) {
        context.store.edit {
            it[server] = serverUrl.trim().trimEnd('/')
            it[this.token] = token.trim()
        }
    }

    suspend fun setPlayOn(packageName: String) {
        context.store.edit { it[playOn] = packageName }
    }

    suspend fun setFeedbackNotification(on: Boolean) {
        context.store.edit { it[notification] = on }
    }

    suspend fun setServerAlerts(on: Boolean) {
        context.store.edit { it[alerts] = on }
    }

    /** The keys of the server warnings already notified, so each one notifies once. */
    suspend fun notifiedWarnings(): Set<String> = context.store.data.first()[notified].orEmpty()

    suspend fun setNotifiedWarnings(keys: Set<String>) {
        context.store.edit { it[notified] = keys }
    }
}
