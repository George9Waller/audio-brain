package com.georgewaller.audiobrain.data

import android.content.Context
import androidx.datastore.preferences.core.booleanPreferencesKey
import androidx.datastore.preferences.core.edit
import androidx.datastore.preferences.core.stringPreferencesKey
import androidx.datastore.preferences.preferencesDataStore
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.flow.map

private val Context.store by preferencesDataStore("settings")

data class Settings(
    val serverUrl: String = "",
    val token: String = "",
    val feedbackNotification: Boolean = true,
) {
    val configured get() = serverUrl.isNotBlank() && token.isNotBlank()
}

class SettingsStore(private val context: Context) {
    private val server = stringPreferencesKey("server_url")
    private val token = stringPreferencesKey("token")
    private val notification = booleanPreferencesKey("feedback_notification")

    val settings: Flow<Settings> = context.store.data.map {
        Settings(it[server].orEmpty(), it[token].orEmpty(), it[notification] ?: true)
    }

    suspend fun current(): Settings = settings.first()

    suspend fun connection(): Pair<String, String> = current().let { it.serverUrl to it.token }

    suspend fun setConnection(serverUrl: String, token: String) {
        context.store.edit {
            it[server] = serverUrl.trim().trimEnd('/')
            it[this.token] = token.trim()
        }
    }

    suspend fun setFeedbackNotification(on: Boolean) {
        context.store.edit { it[notification] = on }
    }
}
